"""Factory helpers for assembling local worker runtimes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from codentum_contracts import RoleSpec

from codentum_harness.runner import CommandRunner
from codentum_harness.worker import LocalWorkerRuntime, WorkerContextLoader, WorkerRunner

__all__ = [
    "LocalWorkerRuntimeConfig",
    "RunnerConfig",
    "build_local_worker_runtime",
    "build_runner",
]


@dataclass(frozen=True, slots=True)
class RunnerConfig:
    """Serializable runner selection for the local harness composition root."""

    kind: Literal["none", "command"] = "none"
    command: Sequence[str] = ()
    timeout_seconds: float = 900.0
    env: Mapping[str, str] | None = None

    @classmethod
    def command_runner(
        cls,
        command: Sequence[str],
        *,
        timeout_seconds: float = 900.0,
        env: Mapping[str, str] | None = None,
    ) -> RunnerConfig:
        return cls(
            kind="command",
            command=tuple(command),
            timeout_seconds=timeout_seconds,
            env=None if env is None else dict(env),
        )


@dataclass(frozen=True, slots=True)
class LocalWorkerRuntimeConfig:
    """Configuration for creating a local WorkerRuntime implementation."""

    repo_root: Path | str
    runner: RunnerConfig | None = None
    context_char_budget: int | None = None


def build_runner(config: RunnerConfig | None) -> WorkerRunner | None:
    """Create the WorkerRunner selected by config."""

    if config is None or config.kind == "none":
        return None
    if config.kind == "command":
        if not config.command:
            raise ValueError("command runner requires a non-empty command")
        if config.timeout_seconds <= 0:
            raise ValueError("command runner timeout_seconds must be positive")
        return CommandRunner(
            tuple(config.command),
            timeout_seconds=config.timeout_seconds,
            env=config.env,
        )
    raise ValueError(f"unsupported runner kind: {config.kind}")


def build_local_worker_runtime(
    config: LocalWorkerRuntimeConfig,
    *,
    role_specs: tuple[RoleSpec, ...] | None = None,
    context_loader: WorkerContextLoader | None = None,
) -> LocalWorkerRuntime:
    """Assemble LocalWorkerRuntime without exposing harness internals to callers."""

    if config.context_char_budget is not None and config.context_char_budget <= 0:
        raise ValueError("context_char_budget must be positive")
    return LocalWorkerRuntime(
        repo_root=config.repo_root,
        runner=build_runner(config.runner),
        role_specs=role_specs,
        context_loader=context_loader,
        context_char_budget=config.context_char_budget,
    )
