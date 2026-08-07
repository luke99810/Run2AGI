"""Scan readable workspace files and every reachable Git blob for credentials."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Final

MAX_FILE_BYTES: Final = 2 * 1024 * 1024
SKIP_DIRECTORIES: Final = frozenset(
    {".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".ruff_cache", ".cache"}
)
BINARY_EXTENSIONS: Final = frozenset(
    {
        ".7z", ".dll", ".docx", ".exe", ".gif", ".gz", ".ico", ".jpeg", ".jpg", ".mp4",
        ".pdf", ".png", ".pptx", ".pyc", ".so", ".ttf", ".webp", ".woff", ".woff2", ".xlsx", ".zip",
    }
)

RULES: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("anthropic", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("openai-like", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("gitlab-token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}")),
    ("slack-token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.")),
    (
        "assigned-secret",
        re.compile(
            r"\b(?:api[_-]?key|secret|token|passwd|password|access[_-]?key)"
            r"\s*[:=]\s*[\"']([^\"'\s]{16,})[\"']",
            re.IGNORECASE,
        ),
    ),
)
PLACEHOLDER = re.compile(
    r"^(?:REPLACE_ME|CHANGE_?ME|YOUR_|EXAMPLE|DUMMY|PLACEHOLDER|TEST[_-]?KEY|<|\$\{|x{6,}$)",
    re.IGNORECASE,
)


class ScanUnavailable(RuntimeError):
    """The mandatory scan could not prove that a required input was inspected."""


@dataclass(frozen=True, slots=True)
class Finding:
    source: str
    path: str
    line: int
    rule: str
    redacted_preview: str


@dataclass(frozen=True, slots=True)
class ScanReport:
    root: Path
    worktree_files: int
    history_blobs: int
    findings: tuple[Finding, ...]

    @property
    def passed(self) -> bool:
        return not self.findings


def redact(value: str) -> str:
    if len(value) < 12:
        return f"<redacted:{len(value)}>"
    return f"{value[:4]}…{value[-4:]} ({len(value)} chars)"


def scan_text(text: str, *, source: str, path: str) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    for rule, pattern in RULES:
        for match in pattern.finditer(text):
            hit = match.group(1) if match.lastindex else match.group(0)
            if PLACEHOLDER.match(hit):
                continue
            findings.append(
                Finding(
                    source=source,
                    path=path,
                    line=text.count("\n", 0, match.start()) + 1,
                    rule=rule,
                    redacted_preview=redact(hit),
                )
            )
    return tuple(findings)


def scan_worktree(root: Path) -> tuple[int, tuple[Finding, ...]]:
    resolved = root.resolve(strict=True)
    if not resolved.is_dir():
        raise ScanUnavailable("scan root is not a directory")
    scanned = 0
    findings: list[Finding] = []
    for current, directories, filenames in os.walk(resolved, followlinks=False):
        directories[:] = sorted(name for name in directories if name not in SKIP_DIRECTORIES)
        current_path = Path(current)
        for filename in sorted(filenames):
            path = current_path / filename
            if path.is_symlink() or path.suffix.lower() in BINARY_EXTENSIONS:
                continue
            try:
                size = path.stat().st_size
                if size > MAX_FILE_BYTES:
                    continue
                raw = path.read_bytes()
                if b"\0" in raw:
                    continue
                text = raw.decode("utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            scanned += 1
            relative = path.relative_to(resolved).as_posix()
            findings.extend(scan_text(text, source="worktree", path=relative))
    return scanned, tuple(findings)


def _git(root: Path, args: list[str]) -> str:
    try:
        result = subprocess.run(  # noqa: S603 - fixed git executable and explicit argv
            ["git", *args],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
    except FileNotFoundError as exc:
        raise ScanUnavailable("git executable is unavailable") from exc
    except subprocess.CalledProcessError as exc:
        raise ScanUnavailable("git repository history is unavailable") from exc
    return result.stdout


def scan_git_history(root: Path) -> tuple[int, tuple[Finding, ...]]:
    resolved = root.resolve(strict=True)
    if _git(resolved, ["rev-parse", "--is-inside-work-tree"]).strip() != "true":
        raise ScanUnavailable("scan root is not a Git worktree")
    try:
        _git(resolved, ["rev-parse", "--verify", "HEAD"])
    except ScanUnavailable as exc:
        raise ScanUnavailable("Git history has no commits; history scan cannot pass") from exc
    object_lines = _git(resolved, ["rev-list", "--objects", "--all"]).splitlines()
    objects: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line in object_lines:
        object_id, separator, path = line.partition(" ")
        if not separator or object_id in seen:
            continue
        seen.add(object_id)
        objects.append((object_id, path))
    try:
        process = subprocess.Popen(  # noqa: S603 - fixed git executable and explicit argv
            ["git", "cat-file", "--batch"],
            cwd=resolved,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
        )
    except FileNotFoundError as exc:
        raise ScanUnavailable("git executable is unavailable") from exc
    if process.stdin is None or process.stdout is None:
        process.kill()
        raise ScanUnavailable("git object reader did not expose pipes")
    scanned = 0
    findings: list[Finding] = []
    try:
        for object_id, path in objects:
            process.stdin.write(object_id.encode("ascii") + b"\n")
            process.stdin.flush()
            object_type, size = _read_batch_header(process.stdout)
            content = process.stdout.read(size)
            process.stdout.read(1)  # batch record separator
            if object_type != "blob" or size > MAX_FILE_BYTES or b"\0" in content:
                continue
            if Path(path).suffix.lower() in BINARY_EXTENSIONS:
                continue
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                continue
            scanned += 1
            findings.extend(scan_text(text, source=f"git:{object_id[:12]}", path=path))
    except (BrokenPipeError, OSError, ValueError) as exc:
        raise ScanUnavailable("Git history object stream failed") from exc
    finally:
        process.stdin.close()
        process.stdout.close()
        try:
            return_code = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            return_code = -1
    if return_code != 0:
        raise ScanUnavailable("Git history object scan did not complete")
    return scanned, tuple(findings)


def _read_batch_header(stream: BinaryIO) -> tuple[str, int]:
    header = stream.readline().decode("ascii", errors="strict").strip()
    parts = header.rsplit(" ", 2)
    if len(parts) != 3 or parts[1] == "missing":
        raise ValueError("unexpected git cat-file batch header")
    return parts[1], int(parts[2])


def scan_repository(root: Path) -> ScanReport:
    worktree_files, worktree_findings = scan_worktree(root)
    history_blobs, history_findings = scan_git_history(root)
    unique: dict[tuple[str, str, int, str], Finding] = {}
    for finding in (*worktree_findings, *history_findings):
        key = (finding.source, finding.path, finding.line, finding.rule)
        unique[key] = finding
    return ScanReport(
        root=root.resolve(),
        worktree_files=worktree_files,
        history_blobs=history_blobs,
        findings=tuple(unique.values()),
    )
