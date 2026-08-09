"""Run an external command inside a worker workspace."""

from __future__ import annotations

import json
import locale
import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from shutil import which

from codentum_contracts import EvidenceRef
from codentum_contracts.interfaces import (
    FailureCode,
    SpawnRequest,
    WorkerCompleted,
    WorkerFailed,
    WorkerOutcome,
)

__all__ = [
    "CommandRunner",
]


@dataclass(frozen=True, slots=True)
class CommandRunner:
    """WorkerRunner adapter for local command-line coding agents."""

    command: Sequence[str]
    timeout_seconds: float = 900.0
    env: Mapping[str, str] | None = None

    def __call__(self, req: SpawnRequest) -> WorkerOutcome:
        if not self.command:
            return WorkerFailed(
                reason_code=FailureCode.RUNTIME_ERROR,
                detail="runner command must not be empty",
                evidence=(),
                spent_cny=0.0,
            )

        workspace = Path(req.workspace)
        worker_id = f"{req.packet_id}-attempt-{req.attempt}"
        evidence_dir = workspace / ".codentum" / "evidence" / worker_id / "runner"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        prompt_dir = evidence_dir.parent / "prompt"

        argv = tuple(
            _render_arg(
                arg,
                req=req,
                worker_id=worker_id,
                evidence_dir=evidence_dir,
                prompt_dir=prompt_dir,
            )
            for arg in self.command
        )
        env = os.environ.copy()
        if self.env is not None:
            env.update(self.env)

        try:
            proc = subprocess.run(  # noqa: S603 - argv is explicit and shell=False; command is user configured.
                argv,
                cwd=workspace,
                capture_output=True,
                # ★ 故意不用 text=True/encoding= —— 解码交给 _coerce_output。
                #   理由见那里：外部工具的输出编码不受我们控制，
                #   而 subprocess 的解码失败会静默杀死读取线程。
                timeout=self.timeout_seconds,
                env=env,
                check=False,
            )
        except FileNotFoundError as exc:
            evidence = _write_result(
                evidence_dir,
                {
                    "argv": list(argv),
                    "error": "command_not_found",
                    "detail": str(exc),
                },
            )
            return WorkerFailed(
                reason_code=FailureCode.RUNTIME_ERROR,
                detail=f"runner command not found: {argv[0]}",
                evidence=(evidence,),
                spent_cny=0.0,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = _coerce_output(exc.stdout)
            stderr = _coerce_output(exc.stderr)
            evidence = _write_outputs_and_result(
                evidence_dir,
                argv=argv,
                stdout=stdout,
                stderr=stderr,
                result={
                    "status": "timeout",
                    "timeout_seconds": self.timeout_seconds,
                },
            )
            return WorkerFailed(
                reason_code=FailureCode.TIMEOUT,
                detail=f"runner command timed out after {self.timeout_seconds:g}s",
                evidence=(evidence,),
                spent_cny=0.0,
            )

        evidence = _write_outputs_and_result(
            evidence_dir,
            argv=argv,
            stdout=_coerce_output(proc.stdout),
            stderr=_coerce_output(proc.stderr),
            result={
                "status": "completed" if proc.returncode == 0 else "failed",
                "exit_code": proc.returncode,
            },
        )
        if proc.returncode != 0:
            return WorkerFailed(
                reason_code=FailureCode.MODEL_ERROR,
                detail=f"runner command exited with code {proc.returncode}",
                evidence=(evidence,),
                spent_cny=0.0,
            )

        return WorkerCompleted(
            evidence=(evidence,),
            spent_cny=0.0,
            touched_paths=_git_changed_paths(workspace),
        )


def _render_arg(
    arg: str,
    *,
    req: SpawnRequest,
    worker_id: str,
    evidence_dir: Path,
    prompt_dir: Path,
) -> str:
    return arg.format(
        attempt=req.attempt,
        evidence_dir=str(evidence_dir),
        packet_id=req.packet_id,
        prompt_dir=str(prompt_dir),
        prompt_manifest=str(prompt_dir / "manifest.json"),
        role=req.role,
        system_prompt=str(prompt_dir / "system.md"),
        user_prompt=str(prompt_dir / "user.md"),
        worker_id=worker_id,
        workspace=req.workspace,
    )


def _write_outputs_and_result(
    evidence_dir: Path,
    *,
    argv: Sequence[str],
    stdout: str,
    stderr: str,
    result: dict[str, object],
) -> EvidenceRef:
    (evidence_dir / "stdout.txt").write_text(stdout, encoding="utf-8")
    (evidence_dir / "stderr.txt").write_text(stderr, encoding="utf-8")
    return _write_result(
        evidence_dir,
        {
            "argv": list(argv),
            "stderr_path": "stderr.txt",
            "stdout_path": "stdout.txt",
            **result,
        },
    )


def _write_result(evidence_dir: Path, result: dict[str, object]) -> EvidenceRef:
    (evidence_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    worker_evidence = evidence_dir.parent
    return EvidenceRef(f"file:{(evidence_dir / 'result.json').relative_to(worker_evidence).as_posix()}")


def _coerce_output(value: bytes | str | None) -> str:
    """把子进程的原始输出变成一个**一定拿得到**的字符串。

    ★ 为什么不直接让 subprocess 用 text=True/encoding="utf-8" 解码：
      被调起来的是任意外部工具，没人保证它按 UTF-8 输出。Windows 上它多半
      用控制台码页（cp936 等）。而 subprocess 的解码发生在**读取线程**里，
      一个 UnicodeDecodeError 就会让那个线程死掉、stdout 变成 None，
      最后在写证据时炸出 "data must be str, not NoneType" —— 报错位置
      和真因隔着十万八千里。所以这里改成拿字节、自己按顺序试。

    ★ 顺带把换行统一成 \\n。丢掉 text=True 就等于丢掉 universal newlines，
      而证据是内容寻址的：同一次执行在 Windows 和 Linux 上算出不同摘要，
      等于摘要失去意义。
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.replace("\r\n", "\n").replace("\r", "\n")

    text: str | None = None
    for encoding in _output_encodings():
        try:
            text = value.decode(encoding)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if text is None:
        # 全都不认：兜底成不会失败的解码，宁可有替换符也不能丢掉整段输出
        text = value.decode("utf-8", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _output_encodings() -> tuple[str, ...]:
    """解码顺序：UTF-8 优先，其次本机首选编码。"""
    encodings = ["utf-8"]
    preferred = locale.getpreferredencoding(False)
    if preferred and preferred.lower().replace("-", "") not in ("utf8",):
        encodings.append(preferred)
    return tuple(encodings)


def _git_changed_paths(workspace: Path) -> tuple[str, ...]:
    git = which("git")
    if git is None:
        return ()

    try:
        raw = subprocess.run(  # noqa: S603 - fixed git invocation, shell=False.
            [git, "-C", str(workspace), "status", "--porcelain", "--untracked-files=all"],
            check=True,
            capture_output=True,
            # ★ 同上：git 会原样吐出文件名的字节，别让它杀死读取线程
        ).stdout
    except (FileNotFoundError, subprocess.CalledProcessError):
        return ()
    out = _coerce_output(raw)

    paths: list[str] = []
    for line in out.splitlines():
        if len(line) >= 4:
            paths.append(line[3:])
    return tuple(sorted(paths))
