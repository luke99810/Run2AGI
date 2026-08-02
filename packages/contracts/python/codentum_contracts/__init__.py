"""codentum_contracts —— 契约层

所有跨模块的类型、schema、接口签名。★ 只有 A 能写。

    from codentum_contracts import WorkPacket, WorkerRuntime, dump_state

两个子模块的分工判据 —— **它会不会出现在磁盘上的某个 JSON 里？**

    会  → state.py       ❌ 生成物，由 scripts/gen_types.py 从 schemas/ 生成
    不会 → interfaces.py  ✓ 手写（行为契约，JSON Schema 表达不了方法签名）

需要改契约 → 提 ContractChangeRequest，不要自己改，
也【不要】在自己的 package 里另建一份"临时类型"绕过去 —— 后者更糟，它会静默漂移。

★ 已于 2026-08-02 冻结（boundaries.yaml: frozen_at）。
"""

from .interfaces import (
    AbortReason,
    Artifact,
    ArtifactKind,
    ArtifactMeta,
    ArtifactRef,
    ArtifactStore,
    BudgetGrantRuntime,
    CheckpointRef,
    CostEstimate,
    CostLedger,
    FailureCode,
    MemoryEntry,
    MemoryIndex,
    MemoryLevel,
    ModelGateway,
    ModelRequest,
    ModelResponse,
    ModelSession,
    MountSpec,
    RetrievalMode,
    RetrievalQuery,
    RetrievalResult,
    SpawnRequest,
    Usage,
    WorkerAborted,
    WorkerCompleted,
    WorkerFailed,
    WorkerHandle,
    WorkerOutcome,
    WorkerRuntime,
)
from .state import (
    Acceptance,
    BudgetFile,
    BudgetGrant,
    DecisionRecord,
    DependencyGraph,
    Digest,
    Effort,
    Evidence,
    EvidenceRef,
    GraphFile,
    KnowledgeEdge,
    KnowledgeFile,
    ModelId,
    ModelRouting,
    OwnershipGraph,
    PacketId,
    PacketKind,
    PacketState,
    PathLock,
    Provenance,
    ProvenanceEdge,
    RoleId,
    RoleSpec,
    Timestamp,
    WorkPacket,
    dump_state,
)

__all__ = [
    "AbortReason", "Acceptance", "Artifact", "ArtifactKind", "ArtifactMeta", "ArtifactRef",
    "ArtifactStore", "BudgetFile", "BudgetGrant", "BudgetGrantRuntime", "CheckpointRef",
    "CostEstimate", "CostLedger", "DecisionRecord", "DependencyGraph", "Digest", "Effort",
    "Evidence", "EvidenceRef", "FailureCode", "GraphFile", "KnowledgeEdge", "KnowledgeFile",
    "MemoryEntry", "MemoryIndex", "MemoryLevel", "ModelGateway", "ModelId", "ModelRequest",
    "ModelResponse", "ModelRouting", "ModelSession", "MountSpec", "OwnershipGraph", "PacketId",
    "PacketKind", "PacketState", "PathLock", "Provenance", "ProvenanceEdge", "RetrievalMode",
    "RetrievalQuery", "RetrievalResult", "RoleId", "RoleSpec", "SpawnRequest", "Timestamp",
    "Usage", "WorkPacket", "WorkerAborted", "WorkerCompleted", "WorkerFailed", "WorkerHandle",
    "WorkerOutcome", "WorkerRuntime", "dump_state",
]
