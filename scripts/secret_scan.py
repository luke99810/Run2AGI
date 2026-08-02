#!/usr/bin/env python
"""secret_scan —— 凭证泄漏扫描（工作区 + ★ git 历史）

════════════════════════════════════════════════════════════════
 ★ 这个脚本没有跳过开关
════════════════════════════════════════════════════════════════

没有 --force，没有 --skip，没有"这次先过一下"。

一旦可豁免，它【在最需要它的那天一定会被豁免】——
因为那天你正赶着推一个 deadline 前的版本，而它恰好在报警。

════════════════════════════════════════════════════════════════
 ★ 为什么必须扫 git 历史，不能只扫工作区
════════════════════════════════════════════════════════════════

历史里的密钥【不会因为你后来删了文件就消失】。
仓库一旦推出去，任何人 clone 下来 `git log -p` 就能翻到。
要真正清掉得用 git filter-repo 或 BFG 重写历史 —— 那时已经太晚，
正确的动作是：立刻吊销那把密钥。

依赖：零。用法：
    python scripts/secret_scan.py                 工作区 + 全部历史
    python scripts/secret_scan.py --staged        只扫暂存区（pre-commit 用）
    python scripts/secret_scan.py --history-only  只扫历史（推送前用）
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib.console import setup_console  # noqa: E402

setup_console()

ROOT = Path(__file__).resolve().parent.parent

# ── 规则 ──────────────────────────────────────────────────────
# 宁可多报也不漏报：假阳性的代价是看一眼，漏报的代价是一把泄漏的密钥。
RULES: list[tuple[str, re.Pattern[str], str]] = [
    ("openai-like", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), "OpenAI / DeepSeek 风格密钥（sk-…）"),
    ("anthropic", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"), "Anthropic 密钥"),
    ("aws-akid", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS Access Key ID"),
    ("github-pat", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "GitHub Token"),
    ("gitlab-pat", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}"), "GitLab Token"),
    ("slack", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"), "Slack Token"),
    ("google-api", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "Google API Key"),
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"), "私钥块"),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\."), "JWT"),
    (
        "assigned-secret",
        re.compile(
            r"\b(?:api[_-]?key|secret|token|passwd|password|access[_-]?key)\s*[:=]\s*[\"']([^\"'\s]{16,})[\"']",
            re.IGNORECASE,
        ),
        "疑似硬编码的密钥赋值",
    ),
]

PLACEHOLDER = re.compile(
    r"^(REPLACE_ME|CHANGE_?ME|YOUR_|<|\$\{|x{3,}$|placeholder|example|dummy|test[_-]?key)", re.IGNORECASE
)

SKIP_DIRS = {
    ".git", "node_modules", "dist", "build", "out", "release", ".next",
    "coverage", ".venv", "venv", "__pycache__", ".cache", "tmp", "temp", ".mypy_cache", ".ruff_cache",
}
BINARY_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz",
    ".exe", ".dll", ".so", ".dylib", ".woff", ".woff2", ".ttf", ".mp4",
    ".pptx", ".docx", ".xlsx", ".pyc",
}
MAX_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Finding:
    where: str
    line: int
    rule: str
    desc: str
    preview: str
    context: str


findings: list[Finding] = []
warnings: list[str] = []
SELF = Path(__file__).name


def redact(s: str) -> str:
    """★ 报告里不打印完整密钥 —— 否则报告本身成了泄漏渠道（日志、CI 输出、截图）。"""
    if len(s) <= 12:
        return s[:3] + "***"
    return f"{s[:6]}…{s[-4:]}（共 {len(s)} 字符）"


def scan_text(text: str, where: str) -> None:
    # 跳过本文件自身的规则定义（否则规则正则会匹配到自己）
    if where.endswith(f"scripts/{SELF}"):
        return
    lines = text.split("\n")
    for rule_id, pat, desc in RULES:
        for m in pat.finditer(text):
            hit = m.group(1) if m.lastindex else m.group(0)
            if PLACEHOLDER.match(hit):
                continue
            line_no = text[: m.start()].count("\n") + 1
            findings.append(
                Finding(where, line_no, rule_id, desc, redact(hit), lines[line_no - 1].strip()[:100])
            )


def walk(d: Path) -> None:
    for p in sorted(d.iterdir()):
        if p.name in SKIP_DIRS:
            continue
        if p.is_dir():
            walk(p)
        elif p.is_file() and p.suffix.lower() not in BINARY_EXT:
            try:
                if p.stat().st_size > MAX_BYTES:
                    continue
                text = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if "\0" in text:
                continue
            scan_text(text, p.relative_to(ROOT).as_posix())


def git(args: list[str]) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    ).stdout


def is_git_repo() -> bool:
    try:
        git(["rev-parse", "--git-dir"])
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def has_commits() -> bool:
    try:
        git(["rev-parse", "--verify", "HEAD"])
        return True
    except subprocess.CalledProcessError:
        return False


def main() -> None:
    args = set(sys.argv[1:])

    if "--staged" in args:
        if not is_git_repo():
            print("\n✗ secret-scan 失败：--staged 需要 git 仓库\n", file=sys.stderr)
            raise SystemExit(2)
        files = [f for f in git(["diff", "--cached", "--name-only", "--diff-filter=ACM"]).splitlines() if f]
        for f in files:
            p = ROOT / f
            if p.exists() and p.suffix.lower() not in BINARY_EXT:
                try:
                    scan_text(p.read_text(encoding="utf-8"), f"暂存区:{f}")
                except UnicodeDecodeError:
                    continue
        print(f"扫描暂存区 {len(files)} 个文件")
    else:
        if "--history-only" not in args:
            walk(ROOT)
            print("扫描工作区 … 完成")

        if not is_git_repo():
            warnings.append(
                "⚠️ 还不是 git 仓库，【历史未被扫描】（不是通过）。\n"
                "     `git init` 并产生提交后，务必在推送 Gitee 前重跑一次。\n"
                "     ★ 历史里的密钥不会因为后来删了文件就消失。"
            )
        elif not has_commits():
            # ★ 空仓库要单独识别，否则"扫了 0 个对象"会显示成一次成功的历史扫描。
            #   零输入的绿灯看起来像证明，实际什么都没证明。
            warnings.append(
                "⚠️ 仓库还没有任何提交，【历史未被扫描】（不是通过）。\n"
                "     首次提交后重跑；★ 推 Gitee 之前必须跑一次带历史的完整扫描。"
            )
        else:
            # 扫描所有 blob 对象 —— 覆盖已被后续提交删掉的内容
            scanned = 0
            for line in git(["rev-list", "--objects", "--all"]).splitlines():
                sha, _, path = line.partition(" ")
                if not path or Path(path).suffix.lower() in BINARY_EXT:
                    continue
                try:
                    content = git(["cat-file", "-p", sha])
                except subprocess.CalledProcessError:
                    continue
                if len(content) > MAX_BYTES or "\0" in content:
                    continue
                scan_text(content, f"git历史:{path}@{sha[:8]}")
                scanned += 1
            print(f"扫描 git 历史 {scanned} 个对象 … 完成")

    for w in warnings:
        print(f"\n  {w}")

    if findings:
        print(f"\n✗ secret-scan：发现 {len(findings)} 处疑似凭证\n", file=sys.stderr)
        for f in findings:
            print(f"  {f.where}:{f.line}", file=sys.stderr)
            print(f"    规则：{f.rule}（{f.desc}）", file=sys.stderr)
            print(f"    命中：{f.preview}", file=sys.stderr)
            if f.context:
                print(f"    上下文：{f.context}", file=sys.stderr)
            print("", file=sys.stderr)
        print("  ────────────────────────────────────────────────", file=sys.stderr)
        print("  ★ 这道门禁没有跳过开关。\n", file=sys.stderr)
        print("  确实是密钥 → ① 立刻吊销它（删文件不等于安全）", file=sys.stderr)
        print("                ② 从工作区移除，改用 .env / 加密存储", file=sys.stderr)
        print("                ③ 若已进 git 历史，用 git filter-repo 或 BFG 重写", file=sys.stderr)
        print("  是误报     → 把值换成明确的占位符（REPLACE_ME_xxx / YOUR_KEY_HERE）\n", file=sys.stderr)
        raise SystemExit(1)

    print(f"\n✓ secret-scan 通过：未发现疑似凭证（{len(RULES)} 条规则）")
    if warnings:
        print("  ⚠️ 但历史尚未被扫描，见上方提示。")


if __name__ == "__main__":
    main()
