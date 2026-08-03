"""路径锁 —— 不变量 I1「单写者」的强制点。

对外只暴露 LockTable 与它的结果类型；前缀树是实现细节。
"""

from codentum_control_plane.locks.path_lock import (
    AcquireResult,
    LockTable,
    PathConflict,
    normalize_path,
)

__all__ = ["AcquireResult", "LockTable", "PathConflict", "normalize_path"]
