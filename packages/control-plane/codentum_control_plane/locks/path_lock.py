"""路径锁 —— 不变量 I1「单写者」的强制实现。

════════════════════════════════════════════════════════════════
 这段代码承载的是什么
════════════════════════════════════════════════════════════════

中心主张：**可靠性来自不变量，不来自提示词。**
I1（任一路径同一时刻只被一个 in_progress packet 拥有）是六条不变量里
唯一「破坏后不可恢复」的一条 —— 两个 packet 同时写一段路径，合并冲突
和互相覆盖都已经发生了，事后没有任何办法还原。

所以这里**零 LLM、零网络、零 I/O**，纯确定性代码，并且默认选择「宁可
误拒，不可误放」：任何拿不准的输入一律当冲突处理。

════════════════════════════════════════════════════════════════
 为什么是前缀树而不是一个 set
════════════════════════════════════════════════════════════════

I1 要禁止的不是「同一条路径被锁两次」，而是**任意两条锁的 pathPrefix
互为前缀关系**（graph.schema.json 里写死的那句）。用 set 只能挡住完全
相同的路径，挡不住 `packages/` 与 `packages/control-plane/` 这种父子
关系 —— 而父子重叠恰恰是真实并行开发里最常见的冲突形态。

前缀树按【路径分段】建树，一次 O(深度) 的下降同时回答三个问题：
  · 路上有没有节点持锁     → 祖先冲突（别人锁了我的上层目录）
  · 终点节点自己持不持锁   → 同路径冲突
  · 终点子树里有没有锁     → 后代冲突（别人锁了我的下层目录）

★ 分段匹配不是可选的优化，是正确性要求。
  字符串 startswith 会把 `src/foo` 和 `src/foobar` 判成冲突 —— 方向上
  是安全的（误拒），但它会平白吃掉并行度，而「并行度可计算」正是这套
  设计被选中的三条理由之一。误拒在这里不是无害的保守。
"""

from __future__ import annotations

import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from codentum_contracts.state import OwnershipGraph, PacketId, PathLock, Timestamp

__all__ = [
    "AcquireResult",
    "LockTable",
    "PathConflict",
    "normalize_path",
]


# ════════════════════════════════════════════════════════════════
#  路径归一化 —— 锁的正确性首先取决于「两个写法是不是同一条路径」
# ════════════════════════════════════════════════════════════════

_REJECTED_SEGMENTS = frozenset({"", ".", ".."})


def normalize_path(raw: str) -> str:
    """把仓库相对路径归一成唯一写法。非法输入直接抛 ValueError。

    ★ 这个函数是 I1 的第一道门。如果 `a/b`、`a/b/`、`./a/b`、`a//b`
      被当成四条不同的路径，四个 packet 就能各锁一条、同时写同一个目录 ——
      锁表看起来毫无冲突，而磁盘上已经打架了。归一化的每一条规则都在堵
      一种「同一个位置的不同拼法」。

    ★ 反斜杠统一转成斜杠，而不是报错。团队在 Windows 上开发，`packages\\x`
      是会被自然写出来的；把它和 `packages/x` 当成两条路径是**不安全**的
      方向，转换是安全的方向 —— 拿不准时朝「更容易判成同一条」走。

    ★ `..` 一律拒绝，不做解析。`a/../b` 语义上等于 `b`，但允许它意味着
      锁表里的字符串和它实际指向的位置可以不一致，那样任何审计都没法只看
      锁表就判断谁能写哪儿。让它在入口处就不存在，比事后解析更可靠。
    """
    if not isinstance(raw, str):  # type: ignore[unreachable]  # 运行时来自 JSON，类型可能不可信
        raise ValueError(f"路径必须是字符串，收到 {type(raw).__name__}")

    text = raw.replace("\\", "/")

    if text.startswith("/"):
        raise ValueError(f"路径必须是仓库相对路径，不能以 / 开头：{raw!r}")
    if len(text) >= 2 and text[1] == ":":
        raise ValueError(f"路径必须是仓库相对路径，不能是盘符绝对路径：{raw!r}")

    segments = [s for s in text.split("/") if s not in ("", ".")]
    if not segments:
        raise ValueError(
            f"路径为空：{raw!r}。★ 空路径会被解读成「锁住整个仓库」，"
            f"那必须是一个显式的决定，不能由拼写失误产生。"
        )
    for seg in segments:
        if seg in _REJECTED_SEGMENTS:
            raise ValueError(f"路径不得包含 {seg!r} 段：{raw!r}")

    return "/".join(segments)


def _match_key(normalized: str) -> tuple[str, ...]:
    """用于树内比较的键。

    ★ casefold 是刻意的：Windows 与 macOS 的文件系统大小写不敏感，
      `Src/App.py` 与 `src/app.py` 在那里是同一个文件。若树里把它们当成
      两个节点，两个 packet 就能各锁一个、同时写同一个文件 —— 在团队的
      主力开发平台上直接破掉 I1。

    ★ 代价说清楚：Linux 上这两条是不同文件，我们会把本可并行的它们判成
      冲突。这是明知的误拒，选它是因为反方向的错误（漏判）不可恢复。
      锁表里保存的仍是原始拼写，只有比较用 casefold。
    """
    return tuple(seg.casefold() for seg in normalized.split("/"))


# ════════════════════════════════════════════════════════════════
#  结果类型
# ════════════════════════════════════════════════════════════════

ConflictRelation = Literal["same", "ancestor", "descendant"]
RejectReason = Literal["conflict", "stale_version", "self_overlap"]


@dataclass(frozen=True, slots=True)
class PathConflict:
    """一次被拒绝的申请里，具体是哪条路径撞上了谁。"""

    requested: str
    """申请方要的路径（归一化后）。"""
    held_prefix: str
    """已被持有的那条路径。"""
    held_by: PacketId
    """持有者。"""
    relation: ConflictRelation
    """same=同一条；ancestor=对方锁了我的上层；descendant=对方锁了我的下层。"""


@dataclass(frozen=True, slots=True)
class AcquireResult:
    """申请结果。

    ★ 冲突与版本失配是**预期内**的正常返回，不是异常 —— 调度器每一轮
      都会撞到它们，用异常表达会让正常路径穿过 except 分支。
      而非法路径是**编码错误**，那个才抛。
    """

    ok: bool
    version: int
    """操作后的所有权图版本号。被拒时等于当前版本，未变。"""
    acquired: tuple[str, ...] = ()
    reason: RejectReason | None = None
    conflicts: tuple[PathConflict, ...] = ()


# ════════════════════════════════════════════════════════════════
#  前缀树
# ════════════════════════════════════════════════════════════════


@dataclass(slots=True)
class _Node:
    children: dict[str, _Node] = field(default_factory=dict)
    lock: PathLock | None = None
    locks_below: int = 0
    """本节点及其整棵子树内的锁数量。让「子树里有没有锁」是 O(1) 而不是 O(子树)。"""


class LockTable:
    """所有权图的运行时形态。

    ★ 唯一写者：只有调度器持有它。这一点来自设计文档「所有权图由调度器
      唯一写入」—— 多个写者会让下面这把互斥锁变成分布式共识问题。

    并发控制是两层的，缺一不可：
      · 进程内：`threading.RLock`，保证 check-then-act 不被穿插
      · 跨进程：`version` 乐观锁，读时记版本、写时比对，不一致就让对方重试

    只有互斥锁而没有版本号，两个进程各自读一份状态、各自改、各自写回，
    后写的会静默覆盖先写的 —— 那正是「状态变更在 Git 可见」这个 P0 判据
    最容易出现的假象：文件是变了，但丢了一次变更。
    """

    def __init__(self, *, version: int = 0) -> None:
        self._root = _Node()
        self._by_packet: dict[PacketId, list[str]] = {}
        self._version = version
        self._mutex = threading.RLock()

    # ── 构造 / 序列化 ────────────────────────────────────────

    @classmethod
    def from_ownership(cls, graph: OwnershipGraph) -> LockTable:
        """从冻结契约里的 OwnershipGraph 重建。

        ★ 重建时会重新校验 I1。磁盘上的 graph.json 是可以被手工编辑的，
          把它当成可信输入就等于把不变量的强制点让给了文本编辑器。
        """
        table = cls(version=graph.version)
        for lock in graph.locks:
            result = table.acquire(lock.heldBy, [lock.pathPrefix], at=lock.acquiredAt)
            if not result.ok:
                detail = "；".join(
                    f"{c.requested} 与 {c.held_prefix}（{c.held_by}）{c.relation}" for c in result.conflicts
                )
                raise ValueError(
                    f"所有权图自身违反 I1，无法载入：{detail}。"
                    f"★ 这说明写出这份 graph.json 的那条路径绕过了锁表 —— 先查那里，不要在这里放行。"
                )
        table._version = graph.version  # acquire 会推进版本，载入不算变更
        return table

    def to_ownership(self) -> OwnershipGraph:
        """导出为契约类型，用于写回 graph.json。

        ★ 按 pathPrefix 排序输出。这不是洁癖：状态要提交进 Git，顺序不稳定
          会让「没有实质变化的两次导出」产生 diff，而 P0 判据恰恰是靠 Git
          的 diff 来证明状态推进的。噪声 diff 会让那个证据失去意义。
        """
        with self._mutex:
            locks = sorted(self._iter_locks(self._root), key=lambda lk: lk.pathPrefix)
            return OwnershipGraph(locks=tuple(locks), version=self._version)

    @property
    def version(self) -> int:
        with self._mutex:
            return self._version

    # ── 查询 ─────────────────────────────────────────────────

    def holder_of(self, path: str) -> PacketId | None:
        """谁实际控制这条路径（含被祖先目录锁覆盖的情况）。"""
        key = _match_key(normalize_path(path))
        with self._mutex:
            node = self._root
            for seg in key:
                if node.lock is not None:
                    return node.lock.heldBy
                child = node.children.get(seg)
                if child is None:
                    return None
                node = child
            return node.lock.heldBy if node.lock else None

    def paths_of(self, packet_id: PacketId) -> tuple[str, ...]:
        with self._mutex:
            return tuple(self._by_packet.get(packet_id, ()))

    # ── 申请 / 释放 ──────────────────────────────────────────

    def acquire(
        self,
        packet_id: PacketId,
        paths: Sequence[str],
        *,
        at: Timestamp,
        expected_version: int | None = None,
    ) -> AcquireResult:
        """原子地申请一组路径。

        **全有或全无**：只要有一条撞车，一条都不落地。
        部分成功会让 packet 拿着半套写权限开始干活，而它的验收谓词是按
        全套写的 —— 那种状态既不是成功也不是失败，是最难清理的一种。

        `expected_version` 是跨进程的乐观锁：传了就比对，不一致直接拒绝
        并要求重试。进程内的竞争由互斥锁挡掉，它挡的是「另一个进程在我
        读到状态之后、写回之前也改了状态」。
        """
        normalized = [normalize_path(p) for p in paths]

        with self._mutex:
            if expected_version is not None and expected_version != self._version:
                return AcquireResult(ok=False, version=self._version, reason="stale_version")

            self_overlap = _find_self_overlap(normalized)
            if self_overlap is not None:
                a, b = self_overlap
                return AcquireResult(
                    ok=False,
                    version=self._version,
                    reason="self_overlap",
                    conflicts=(
                        PathConflict(requested=b, held_prefix=a, held_by=packet_id, relation="descendant"),
                    ),
                )

            conflicts = tuple(c for p in normalized for c in self._conflicts_for(p))
            if conflicts:
                return AcquireResult(ok=False, version=self._version, reason="conflict", conflicts=conflicts)

            for p in normalized:
                self._insert(p, PathLock(pathPrefix=p, heldBy=packet_id, acquiredAt=at))
            self._by_packet.setdefault(packet_id, []).extend(normalized)
            self._version += 1
            return AcquireResult(ok=True, version=self._version, acquired=tuple(normalized))

    def release(self, packet_id: PacketId) -> tuple[str, ...]:
        """释放某个 packet 的全部锁。返回被释放的路径。

        ★ 幂等：没有锁时返回空且**不推进版本**。重复释放在崩溃恢复里是
          常态，让它推进版本会制造出「什么都没发生但版本变了」的记录，
          把乐观锁的语义搅浑。
        """
        with self._mutex:
            held = self._by_packet.pop(packet_id, [])
            if not held:
                return ()
            for p in held:
                self._remove(p)
            self._version += 1
            return tuple(held)

    def release_expired(self, *, now: datetime, ttl: timedelta) -> tuple[str, ...]:
        """按 TTL 回收超时未释放的锁 —— Worker 崩溃后的兜底。

        ★ TTL 是**策略**不是**状态**，所以它是参数而不是 PathLock 的字段。
          冻结的 schema 里没有 TTL 字段，这是对的：把策略写进状态文件，
          改一次策略就要改一次数据格式，而契约冻结后改格式要走变更窗口。

        ★ acquiredAt 解析失败当作「已过期」处理。方向是刻意的：一条时间戳
          坏掉的锁如果永远不过期，它占住的路径就再也没人能拿到，而这种
          死锁在锁表里看起来完全正常。
        """
        expired: list[PacketId] = []
        with self._mutex:
            for lock in self._iter_locks(self._root):
                if _is_expired(lock.acquiredAt, now=now, ttl=ttl):
                    expired.append(lock.heldBy)
            released: list[str] = []
            for pid in dict.fromkeys(expired):
                released.extend(self.release(pid))
            return tuple(released)

    # ── 内部 ─────────────────────────────────────────────────

    def _conflicts_for(self, normalized: str) -> list[PathConflict]:
        key = _match_key(normalized)
        node = self._root
        for seg in key:
            if node.lock is not None:
                return [
                    PathConflict(
                        requested=normalized,
                        held_prefix=node.lock.pathPrefix,
                        held_by=node.lock.heldBy,
                        relation="ancestor",
                    )
                ]
            child = node.children.get(seg)
            if child is None:
                return []
            node = child

        if node.lock is not None:
            return [
                PathConflict(
                    requested=normalized,
                    held_prefix=node.lock.pathPrefix,
                    held_by=node.lock.heldBy,
                    relation="same",
                )
            ]
        if node.locks_below > 0:
            # 只在拒绝路径上才走这次子树扫描 —— 让错误信息指出具体是谁，
            # 而不是只说"下面有锁"。可操作的拒绝理由才可能被修好。
            below = next(iter(self._iter_locks(node)), None)
            if below is None:
                # locks_below 说有、子树里却找不到 = 计数器与树本身对不上。
                #
                # ★ 这里必须显式抛，不能让 next() 的 StopIteration 逃进上层的
                #   生成器表达式 —— Python 会把它转成 `RuntimeError: generator
                #   raised StopIteration`，堆栈指向调用点而不是这里，真实原因
                #   （计数器漂了）被完全掩盖。这个坑是在无锁压测里踩到的。
                #
                # ★ 抛而不是当作"无冲突"：计数器漂了说明表已经不可信，
                #   此时放行申请等于在一个坏掉的锁表上继续发写权限。
                raise RuntimeError(
                    f"锁表内部不一致：{normalized!r} 的子树 locks_below={node.locks_below}，"
                    f"但遍历不到任何锁。★ 这通常意味着有人绕过互斥锁并发修改了锁表。"
                )
            return [
                PathConflict(
                    requested=normalized,
                    held_prefix=below.pathPrefix,
                    held_by=below.heldBy,
                    relation="descendant",
                )
            ]
        return []

    def _insert(self, normalized: str, lock: PathLock) -> None:
        node = self._root
        node.locks_below += 1
        for seg in _match_key(normalized):
            node = node.children.setdefault(seg, _Node())
            node.locks_below += 1
        node.lock = lock

    def _remove(self, normalized: str) -> None:
        path: list[tuple[_Node, str]] = []
        node = self._root
        node.locks_below -= 1
        for seg in _match_key(normalized):
            child = node.children[seg]
            path.append((node, seg))
            child.locks_below -= 1
            node = child
        node.lock = None
        # 回收空节点，否则长期运行后树里全是没有锁的枝干
        for parent, seg in reversed(path):
            child = parent.children[seg]
            if child.lock is None and not child.children:
                del parent.children[seg]

    @staticmethod
    def _iter_locks(node: _Node) -> Iterable[PathLock]:
        stack = [node]
        while stack:
            cur = stack.pop()
            if cur.lock is not None:
                yield cur.lock
            stack.extend(cur.children.values())


def _find_self_overlap(normalized: Sequence[str]) -> tuple[str, str] | None:
    """一次申请内部自己就重叠（例如同时要 `src` 和 `src/app`）。

    ★ 选择拒绝而不是自动折叠成 `src`。折叠会让 packet 的 ownsPaths 与它
      实际拿到的锁不一致，而 ownsPaths 同时是 Harness 的挂载依据 ——
      两处不一致就意味着「写权限」和「锁」说的不是同一件事。
      这是 packet 定义写错了，应该让写的人看见。
    """
    keys = [(_match_key(p), p) for p in normalized]
    for i, (ka, pa) in enumerate(keys):
        for kb, pb in keys[i + 1 :]:
            if ka == kb or ka == kb[: len(ka)] or kb == ka[: len(kb)]:
                return (pa, pb) if len(ka) <= len(kb) else (pb, pa)
    return None


def _is_expired(acquired_at: Timestamp, *, now: datetime, ttl: timedelta) -> bool:
    try:
        stamp = datetime.fromisoformat(acquired_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=now.tzinfo)
    return now - stamp > ttl
