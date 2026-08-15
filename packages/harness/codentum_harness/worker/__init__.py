"""Worker runtime implementations and Git worktree isolation."""

from .local import LocalWorkerRuntime, WorkerContextLoader, WorkerRoleSpecResolver, WorkerRunner
from .team import (
    AgentTeamsClient,
    AgentTeamsCLIError,
    AgentTeamsDispatchReceipt,
    AgentTeamsDockerCLIClient,
    AgentTeamsDockerCLIConfig,
    AgentTeamsTaskResult,
    AgentTeamsTaskSpec,
    AgentTeamsWorkerSpec,
    AgentTeamsWorkerStatus,
    TeamWorkerRuntime,
)
from .integrate import IntegrationResult, RollbackResult, integrate_worker_result, rollback_worker_result
from .worktree import (
    GitWorktreeManager,
    ProjectInit,
    WorktreeIsolationError,
    ensure_project_initialized,
)

__all__ = [
    "AgentTeamsCLIError",
    "AgentTeamsClient",
    "AgentTeamsDispatchReceipt",
    "AgentTeamsDockerCLIClient",
    "AgentTeamsDockerCLIConfig",
    "AgentTeamsTaskResult",
    "AgentTeamsTaskSpec",
    "AgentTeamsWorkerSpec",
    "AgentTeamsWorkerStatus",
    "GitWorktreeManager",
    "IntegrationResult",
    "RollbackResult",
    "integrate_worker_result",
    "rollback_worker_result",
    "ProjectInit",
    "ensure_project_initialized",
    "LocalWorkerRuntime",
    "TeamWorkerRuntime",
    "WorkerContextLoader",
    "WorkerRoleSpecResolver",
    "WorkerRunner",
    "WorktreeIsolationError",
]
