"""ReconcileLoop —— 控制平面的调和循环。

对标 K8s controller manager。对每个非终态的 WorkPacket，
检查当前状态 → 判断下一步 → 推进状态 → 落盘。

★ 幂等：同一状态跑 N 次 tick 产生同样的结果（副作用只发生一次）。
★ 确定性：零 LLM，零随机，所有判定由代码做出。
★ 不 import codentum_harness：WorkerRuntime 通过构造函数注入。
★ 崩溃恢复：load_state() 从 .codentum/ 目录重建全部内存状态。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from codentum_contracts.interfaces import (
    AbortReason,
    BudgetGrantRuntime,
    MountSpec,
    SpawnRequest,
    WorkerHandle,
    WorkerOutcome,
    WorkerRuntime,
)
from codentum_contracts.state import (
    Acceptance,
    BudgetGrant,
    DecisionRecord,
    DependencyEdge,
    DependencyGraph,
    EvidenceRef,
    ModelRouting,
    OwnershipGraph,
    PacketId,
    PacketState,
    PathLock,
    Provenance,
    WorkPacket,
    dump_state,
)
from codentum_control_plane.locks import LockTable
from codentum_control_plane.gates import GateRunner, register_builtin_gates
from codentum_control_plane.budget import BudgetTracker
from codentum_control_plane.evidence import (
    SYS_EVIDENCE_PREFIX,
    WORKER_FAILED_EVIDENCE_PREFIX,
    is_acceptance_evidence,
)
from codentum_control_plane.guardian import Guardian
from codentum_control_plane.scheduling import (
    ReadyQueueEntry,
    SchedulingConfig,
    build_ready_queue,
    build_scheduling_projection,
    count_packet_states,
    default_scheduling_config,
    load_scheduling_config,
    remaining_capacity,
    under_wip_limit,
)
from codentum_control_plane.state_machine import TransitionTable

from .actions import PacketTransition, ReconcileContext, TickReport


logger = logging.getLogger(__name__)


TERMINAL_STATES: frozenset[PacketState] = frozenset({"accepted", "abandoned"})
"""终态包不再被 reconcile 处理。"""

BOOKKEEPING_PATH_PREFIX = ".codentum/"
"""工作区里属于**系统自己**的目录 —— 判断「干没干活」时必须排除。

★ harness 把 prompt / response / usage / manifest / checkpoints 全写进
  worker 工作区的 `.codentum/evidence/**`。它们确实是「工作区里新增的文件」，
  但它们是**系统写的**，不是 worker 干出来的产物。

  实测：模型一个文件都没建，`touched_paths` 有 10 条，全是这些。
  拿它当「干了活」的判据，等于让系统给自己签字 —— 与 `sys:` 前缀那个洞
  是同一个 bug，只是从 evidence 列表下沉到了 touched_paths。
"""


def _worker_authored_paths(paths: Sequence[str]) -> tuple[str, ...]:
    """滤掉系统自己写进工作区的簿记，只留 worker 真正的产出。

    ★ 同时归一化分隔符：`_git_changed_paths` 走 `git status --porcelain`，
      输出的是正斜杠；但别的 runner 未必。在 Windows 上漏掉这一步，
      `.codentum\\evidence\\...` 会因为前缀匹配不上而被当成真实产出 ——
      **判据在一个平台上有效、在另一个平台上失效，且不报错。**
      这个坑本项目已经踩过两次（EvidenceRef 分隔符、流编码）。
    """
    kept: list[str] = []
    for raw in paths:
        normalized = str(raw).replace("\\", "/")
        # ★ 不要用 `lstrip("./")` —— 它剥的是**字符集合**不是前缀，
        #   `.codentum/x` 会被剥成 `codentum/x`，前缀判定随之失效。
        #   第一版就是这么写的，被本节的测试当场抓住。
        while normalized.startswith("./"):
            normalized = normalized[2:]
        if normalized.startswith(BOOKKEEPING_PATH_PREFIX):
            continue
        kept.append(str(raw))
    return tuple(kept)


MUST_TOUCH_FILES_KINDS: frozenset[str] = frozenset({"impl", "test", "fix"})
"""这几类 packet 干完活必然会改文件，改动数为 0 就是没干活。

★ 为什么不是全部 kind：`review` / `spike` / `design` 本来就可能一个文件都不碰
  （评审的产出是判断，探针的产出是结论）。一刀切会把它们全判成失败 ——
  那不是更严格，是把判据变成噪音。

★ `contract` / `integrate` / `evolve` 暂不列入：前两者确实会改文件，但目前还没有
  真实样本能确认它们的 worker 一定经由 touched_paths 上报；宁可先漏判，
  不要先误判。有样本了再加，加的时候记得先写会红的测试。
"""


# ════════════════════════════════════════════════════════════
#  证据前缀约定
# ════════════════════════════════════════════════════════════
#
# ★ 判据本体已移到 `codentum_control_plane.evidence`，因为门禁层也要用同一份。
#   原来它只写在这里，于是 08-09 那次修复只落到了兜底分支，
#   `gates/builtin.py` 里的四个门禁仍把 `sys:` 簿记算成证据 ——
#   配了 gate_runner 反而比不配更松。详见 evidence.py 的模块注释。
#
# 下面三个名字保留为再导出，外部引用（含测试）不受影响。
_is_acceptance_evidence = is_acceptance_evidence


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ReconcileLoop:
    """调和循环 —— 控制平面的心脏。

    用法：
        loop = ReconcileLoop(state_dir="path/to/.codentum")
        loop.load_state()
        report = loop.tick()          # 推进一轮
        loop.save_state()             # 落盘
    """

    state_dir: str
    "包含 graph.json 和 packets/ 的目录（通常是 .codentum/）。"

    worker_runtime: WorkerRuntime | None = None
    "★ 可选 —— 没有 Worker 时仍可推进依赖、锁等纯确定性状态。"

    transition_table: TransitionTable | None = None
    "★ 若为 None，使用不含角色转换的空表（仅允许 reconcile 内部转换）。"

    gate_runner: GateRunner | None = None
    "★ 门禁执行器。若为 None，review→accepted 仅检查证据是否存在。"

    budget_tracker: BudgetTracker | None = None
    "★ 全局预算追踪器。若为 None，不记录支出也不做全局预算检查。"

    guardian: Guardian | None = None
    "★ 确定性拦截器。若为 None，不执行运行时不变量检查。"

    result_integrator: Callable[[WorkPacket], tuple[bool, str]] | None = None
    """把 worker 产出合回主项目的回调。为 None 时**不合入**。

    ★ 由装配点注入而不是控制平面自己做：合入是 git 操作，
      而控制平面的承诺是「确定性代码，零 LLM，不派生子进程」。
      注入之后控制平面只做一件事 —— **决定什么时候合**（验收通过时）。

    ★ 为 None 时不合入，这件事必须让人看得见：
      packet 会是 accepted 而项目里没有东西，那正是这条缺口原本的样子。
      装配点（EngineService）默认注入真实实现。
    """

    max_running: int | None = None
    """同时处于 running 的 packet 上限（WIP 限制）。None = 不限。

    ★ 这不是性能调优，是**止损**。2026-08-13 实测：8 个 packet 同时
      调模型时出现 `Connection error` —— 并发本身把请求打挂了。
      而失败的那几个会重试，重试又加剧并发，成本和失败率一起上去。

    ★ 精益调度里这就是 WIP 限制：**在制品越多，单件周期越长**。
      控制平面是唯一知道全局有多少 packet 在跑的地方，
      所以这条限制只能在这里执行 —— 放到 worker 侧各自为政拦不住总量。

    ★ 它同时是 `.codentum/scheduling.json` 里 `wipLimits.running` 的**唯一真实来源**。
      没有真正执行的限制，就不该往那个文件里写数字 ——
      写了就是「声明了但没人执行」，而桌面端会照着它渲染。
    """

    # ── 内部状态（由 load_state() 填充）─────────────────────

    _packets: dict[PacketId, WorkPacket] = field(default_factory=dict, init=False)
    _dep_graph: DependencyGraph | None = field(default_factory=lambda: None, init=False)
    _lock_table: LockTable = field(default_factory=LockTable, init=False)
    _active_workers: dict[PacketId, WorkerHandle] = field(default_factory=dict, init=False)
    _tick_count: int = field(default=0, init=False)
    _dirty: bool = field(default=False, init=False)
    _scheduling_config: SchedulingConfig = field(
        default_factory=default_scheduling_config,
        init=False,
    )
    _ready_queue_entries: tuple[ReadyQueueEntry, ...] = field(default=(), init=False)
    _ready_to_start: frozenset[PacketId] = field(default_factory=frozenset, init=False)

    _state_dir_ensured: bool = field(default=False, init=False)
    """是否已经铺过一次状态目录。

    ★ 用来区分「初始化」与「运行中自愈」：第一次什么都不存在是正常的，
      把它也报成 warning 会让每次正常启动都刷一条，真出事那条就淹没在噪音里。
      **告警的价值来自它的稀有。**

    ★ 它**不能**由 `load_state()` 重置 —— 第一版就是加在那里，
      于是每次重新加载状态都把标志清掉，自愈告警再也不响。
    """

    _loop: asyncio.AbstractEventLoop | None = field(default=None, init=False, repr=False)
    """Persistent event loop shared between spawn() and settle() calls.
    
    Must be a single long-lived loop (not asyncio.run() which creates/destroys
    per call) because B's LocalWorkerRuntime spawns background tasks during
    spawn() that must survive until settle().
    """

    # ════════════════════════════════════════════════════════════
    #  状态加载 / 持久化
    # ════════════════════════════════════════════════════════════

    def load_state(self, *, state_dir: str | None = None) -> None:
        """从 .codentum/ 目录加载全部状态。

        ★ 崩溃恢复的唯一入口。只要 .codentum/ 里的文件没坏，
          重启后调用此方法即可恢复。
        """
        if state_dir is not None:
            self.state_dir = state_dir

        root = Path(self.state_dir)

        # 1. 加载 graph.json → 依赖图 + 所有权图
        graph_path = root / "graph.json"
        if graph_path.exists():
            graph_raw = json.loads(graph_path.read_text("utf-8"))
            dep_raw = graph_raw.get("dependency", {})
            self._dep_graph = DependencyGraph(
                nodes=tuple(dep_raw.get("nodes", ())),
                edges=tuple(
                    DependencyEdge(**{"from": e["from"], "to": e["to"]})
                    for e in dep_raw.get("edges", ())
                ),
            )
            ownership = OwnershipGraph(
                locks=tuple(
                    PathLock(
                        pathPrefix=lk["pathPrefix"],
                        heldBy=lk["heldBy"],
                        acquiredAt=lk["acquiredAt"],
                    )
                    for lk in graph_raw.get("ownership", {}).get("locks", ())
                ),
                version=graph_raw.get("ownership", {}).get("version", 0),
            )
            self._lock_table = LockTable.from_ownership(ownership)
        else:
            self._dep_graph = DependencyGraph(nodes=(), edges=())
            self._lock_table = LockTable()

        # 2. 加载所有 packet 文件
        packets_dir = root / "packets"
        self._packets.clear()
        if packets_dir.exists():
            for pf in sorted(packets_dir.glob("*.json")):
                raw = json.loads(pf.read_text("utf-8"))
                packet = WorkPacket(
                    id=raw["id"],
                    kind=raw["kind"],
                    state=raw["state"],
                    role=raw["role"],
                    ownsPaths=tuple(raw.get("ownsPaths", ())),
                    readsPaths=tuple(raw.get("readsPaths", ())),
                    deps=tuple(raw.get("deps", ())),
                    acceptance=Acceptance(
                        kind=raw["acceptance"]["kind"],
                        predicate=raw["acceptance"]["predicate"],
                        threshold=raw["acceptance"].get("threshold"),
                        authoredBy=raw["acceptance"]["authoredBy"],
                    ),
                    budget=BudgetGrant(
                        currency=raw["budget"]["currency"],
                        limitCny=raw["budget"]["limitCny"],
                        spentCny=raw["budget"]["spentCny"],
                        degradationChain=tuple(raw["budget"].get("degradationChain", ())),
                    ),
                    routing=(
                        ModelRouting(
                            model=raw["routing"]["model"],
                            effort=raw["routing"]["effort"],
                            batch=raw["routing"].get("batch"),
                        )
                        if raw.get("routing")
                        else None
                    ),
                    attempts=raw.get("attempts", 0),
                    evidence=tuple(raw.get("evidence", ())),
                    provenance=Provenance(
                        createdBy=raw["provenance"]["createdBy"],
                        createdAt=raw["provenance"]["createdAt"],
                        parent=raw["provenance"].get("parent"),
                    ),
                )
                self._packets[packet.id] = packet

        self._active_workers.clear()
        self._tick_count = 0
        self._scheduling_config = load_scheduling_config(root)
        self._ready_queue_entries = ()
        self._ready_to_start = frozenset()
        self._dirty = False

    def save_state(self) -> None:
        """将当前内存状态写回 .codentum/ 文件。

        ★ 仅当 _dirty=True 时才实际写入。
        ★ 先写 packets 再写 graph —— graph 是依赖图和锁表的汇总。
        """
        if not self._dirty:
            return

        root = Path(self.state_dir)
        packets_dir = root / "packets"
        packets_dir.mkdir(parents=True, exist_ok=True)

        # 写每个 packet 文件
        for pid, packet in self._packets.items():
            pf = packets_dir / f"{pid}.json"
            pf.write_text(
                json.dumps(dump_state(packet), indent=2, ensure_ascii=False) + "\n",
                "utf-8",
            )

        # 写 graph.json（依赖图 + 所有权图）
        #
        # ★ dependency 由 self._packets 推导，而不是回写 self._dep_graph。
        #   _dep_graph 只在 load_state 时从磁盘读入，之后从不参与任何判定
        #   （真正的依赖真相是 packet.deps，见 _try_pending_to_ready），
        #   所以照抄它等于把上一次的磁盘内容原样吐回去：新建的 packet 永远
        #   不会出现在 nodes 里。下游 desktop 的 checkGraphPacketCoherence
        #   会因此报 [partial-write] 并把整个状态目录判为 incoherent。
        #   packets 是唯一权威，graph.dependency 是它的投影。
        nodes = sorted(str(pid) for pid in self._packets)
        edges = sorted(
            (
                {"from": str(dep), "to": str(pid)}
                for pid, packet in self._packets.items()
                for dep in packet.deps
            ),
            key=lambda e: (e["to"], e["from"]),
        )
        ownership = self._lock_table.to_ownership()
        graph_raw: dict[str, Any] = {
            "schemaVersion": 1,
            "dependency": {
                "nodes": nodes,
                "edges": edges,
            },
            "ownership": {
                "locks": [
                    {
                        "pathPrefix": lk.pathPrefix,
                        "heldBy": lk.heldBy,
                        "acquiredAt": lk.acquiredAt,
                    }
                    for lk in ownership.locks
                ],
                "version": ownership.version,
            },
        }
        (root / "graph.json").write_text(
            json.dumps(graph_raw, indent=2, ensure_ascii=False) + "\n",
            "utf-8",
        )

        self._write_budget(root)
        self._write_scheduling(root)
        self.ensure_state_dir()

        self._dirty = False

    # ── .codentum/ 目录形状 ──────────────────────────────────

    def _write_budget(self, root: Path) -> None:
        """把预算落盘到 budget.json。

        ★ 没有这一步的话，spend() 记的账只活在内存里：进程一停就没了，
          桌面端也永远显示不出花了多少。追踪器有 to_file()，
          之前只是没人调用它。

        ★ budget_tracker 为 None 时**不写**。A 此时确实不知道全局预算是多少，
          凭空造一个数字比缺文件更糟 —— 缺文件只是缺，造出来的数字会被当真。
        """
        if self.budget_tracker is None:
            if not (root / "budget.json").exists():
                # 不造数字，但也不能不吭声：这样写出去的 .codentum/ 是不完整的，
                # C 会把整份快照判为 incoherent。让它响，别让它默默发生。
                logger.warning(
                    "未配置 budget_tracker，不写 budget.json —— %s 将缺少该文件，"
                    "桌面端会把这份状态判为不连贯。请在构造 ReconcileLoop 时传入 "
                    "BudgetTracker。",
                    root,
                )
            return
        budget_file = self.budget_tracker.to_file()
        payload = {
            k: v for k, v in dump_state(budget_file).items() if v is not None
        }
        (root / "budget.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            "utf-8",
        )

    def _write_scheduling(self, root: Path) -> None:
        """把 WIP 上限、当前计数和 ready 队列投影到 scheduling.json。"""
        root.mkdir(parents=True, exist_ok=True)
        payload = (
            json.dumps(
                build_scheduling_projection(
                    packets=tuple(self._packets.values()),
                    config=self._scheduling_config,
                    ready_queue=self._ready_queue_entries,
                    selected_to_start=self._ready_to_start,
                ),
                indent=2,
                ensure_ascii=False,
            )
            + "\n"
        )
        target = root / "scheduling.json"
        if target.exists() and target.read_text(encoding="utf-8") == payload:
            return
        target.write_text(payload, encoding="utf-8")

    def _heal_state_dir_if_missing(self) -> None:
        """常态下只做几次 stat；确实缺东西了才走完整的补齐流程。

        ★ 把「检查」与「补齐」分开，是因为两者的频率差着几个数量级：
          检查每轮都要做，补齐几乎永远不触发。
          把它们绑在一起的代价是每轮一次文件写入 —— 实测 25 倍的总耗时。
        """

        root = Path(self.state_dir)
        required = (
            root / "graph.json",
            root / "scheduling.json",
            root / "decisions.jsonl",
            root / "packets",
            root / "evidence",
            root / "knowledge",
        )
        if all(path.exists() for path in required):
            return
        self.ensure_state_dir()

    def ensure_state_dir(self) -> None:
        """把 `.codentum/` 铺成一份**完整且连贯的空状态**。

        ★ `.codentum/` 是 A 与 C 之间唯一的接口，完整形状由
          `fixtures/golden-state/empty` 定义：
          graph.json · budget.json · decisions.jsonl · packets/ · evidence/ · knowledge/

        ★ 这个方法原来是私有的 `_ensure_state_dir_shape`，而且**没做到自己
          文档说的事** —— 注释里列了 graph.json 和 budget.json，代码却只建了
          三个目录和 decisions.jsonl。它又只被 `save_state()` 调用，
          而 save_state 只在 `_dirty` 时才真的写。

          后果是 2026-08-11 实机撞到的那一幕：引擎启动时为了放
          `engine-session.json` 建了 `.codentum/`，桌面端于是看到一个
          **存在但残缺**的状态目录，界面上排开五条
          `[missing] Required state file is missing: ...`。

          ★ 比「目录不存在」更糟：不存在时桌面端会显示「尚未初始化」，
          残缺时它显示的是一串错误 —— **半个状态目录比没有状态目录更坏。**

        ★ 已存在的一律不动，只补缺的。空的 decisions.jsonl 与空的
          evidence/ 本身就是合法状态，这里不是造数据。
        """
        root = Path(self.state_dir)
        healed: list[str] = []

        if not root.exists():
            healed.append(".codentum/")
        root.mkdir(parents=True, exist_ok=True)
        for directory in ("evidence", "knowledge", "packets"):
            target = root / directory
            if not target.is_dir():
                healed.append(f"{directory}/")
            target.mkdir(parents=True, exist_ok=True)

        decisions = root / "decisions.jsonl"
        if not decisions.exists():
            healed.append("decisions.jsonl")
            decisions.write_text("", encoding="utf-8")

        graph = root / "graph.json"
        if not graph.exists():
            healed.append("graph.json")
            graph.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "dependency": {"nodes": [], "edges": []},
                        "ownership": {"locks": [], "version": 0},
                    },
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

        budget_was_missing = not (root / "budget.json").exists()

        # ★ budget.json 仍然遵守「没有 budget_tracker 就不写」——
        #   凭空造一个预算数字比缺文件更糟，缺文件只是缺，造出来的数字会被当真。
        #   （`_write_budget` 在这种情况下会打 warning，那是有意的。）
        self._write_budget(root)

        # ★ 只有**真的写出来了**才算补齐。
        #
        #   第一版直接在写之前 append("budget.json")，于是没有 budget_tracker 时
        #   日志每轮都说「已自动补齐：budget.json」—— 而 `_write_budget` 那种
        #   情况下根本不写，文件依旧不存在。
        #   **声称补了、其实没补** —— 正是这套系统一路在拆的那个病，
        #   这次出现在了修它的代码里。判据是「补完之后它在不在」。
        if budget_was_missing and (root / "budget.json").exists():
            healed.append("budget.json")

        scheduling_was_missing = not (root / "scheduling.json").exists()
        if not scheduling_was_missing:
            self._scheduling_config = load_scheduling_config(root)
        self._write_scheduling(root)
        if scheduling_was_missing and (root / "scheduling.json").exists():
            healed.append("scheduling.json")

        # ★ 补过东西就**出声**。
        #
        #   静默自愈会把「有人在删你的状态」这件事藏起来 —— 而那是个真问题：
        #   2026-08-12 就发生过一次（另一个进程把 .codentum/ 当调试残留清掉，
        #   运行中的桌面端立刻排出六条 [missing]，而引擎毫无察觉，
        #   直到重启才恢复）。
        #
        #   自愈让系统能继续跑，出声让人知道刚才发生过异常。**两者都要。**
        # ★ 只有**已经铺过一次之后**再缺失才算自愈。
        #
        #   第一次调用是初始化 —— 那时什么都不存在是正常的，
        #   把它也报成「已自动补齐」会让每次正常启动都刷一条 warning，
        #   而真出事那条就淹没在噪音里了。
        #   **告警的价值来自它的稀有。**
        if healed and self._state_dir_ensured:
            logger.warning(
                "状态目录在运行中缺失，已自动补齐：%s"
                "（这通常意味着有别的进程动了 .codentum/）",
                "、".join(healed),
            )
        self._state_dir_ensured = True

    # ════════════════════════════════════════════════════════════
    #  调和主循环
    # ════════════════════════════════════════════════════════════

    def _event_loop(self) -> asyncio.AbstractEventLoop:
        """按需拿到那个长期存活的事件循环。

        ★ 原来只在 spawn 分支里 `if self._loop is None: ... new_event_loop()`，
          settle 分支直接用 `self._loop.run_until_complete(...)`。当下调用顺序保证了
          settle 之前一定 spawn 过（`_active_workers` 在 load_state 里被清空），
          所以跑起来没出过事 —— 但那是**调用顺序**给的保证，不是类型给的。
          哪天有人让 settle 能在别的路径上被触达，那里就是一个 AttributeError，
          而且只在恢复场景下偶发。

        ★ 不用 `asyncio.get_event_loop()`：必须是同一个长期存活的循环，
          B 的 LocalWorkerRuntime 在 spawn() 里起的后台任务要活到 settle()。
        """
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
        return self._loop

    def admit(self, packet: WorkPacket) -> None:
        """把一个新 packet 纳入调和范围。**准入校验由调用方负责。**

        ★ 为什么需要这个方法：在它之前，唯一的入口是「先把 packet 文件写进
          `packets/`，再 `load_state()` 重读一遍」。测试这么用没问题，
          产品入口这么用会有一个窗口 —— 校验通过与落盘之间，
          文件已经在磁盘上了但内存里还没有，此时崩溃会留下一个没被校验过的
          packet 等着下次 `load_state()` 读回来。

          于是引擎一度直接写 `loop._packets[...]` 和 `loop._dirty` 两个私有字段。
          能跑，但那等于把「怎么纳入一个 packet」这件事的定义散到了包外面 ——
          哪天这里加了索引或校验，外面那两行不会跟着变，也不会报错。

        ★ 不在这里跑 `AdmissionChecker`：准入需要 RoleSpec，而控制平面
          **不接触 RoleSpec**（见 `_build_spawn_request` 的注释）。
          把它塞进来会让控制平面依赖角色层，破坏那条边界。
          调用方先 check 再 admit，这个顺序由调用方的测试守。
        """
        if packet.id in self._packets:
            raise ValueError(
                f"packet {packet.id} 已存在。重新提交同一个 id 会静默覆盖它的状态与证据 —— "
                f"要重开应当新建 packet 并在 provenance.parent 里标明来源。"
            )
        self._packets[packet.id] = packet
        self._dirty = True

    def tick(self) -> TickReport:
        """执行一轮调和。

        遍历所有非终态 packet，每条最多推进一次。
        返回本轮实际发生的状态变更。
        """
        self._tick_count += 1

        # ★ 每轮都补一次状态目录 —— **自愈，而不是等重启**。
        #
        #   `ensure_state_dir()` 原来只在引擎启动时跑一次。文件一旦在运行中
        #   消失（别的进程清理、误删、外部同步工具），引擎毫无察觉，
        #   桌面端就一直排着 [missing]，**直到有人重启它**。
        #
        #   2026-08-12 实际发生了一次：另一个进程把 `.codentum/` 当调试残留删了，
        #   运行中的界面立刻出现六条 [missing]，而引擎照常 tick、照常写 packet ——
        #   写进了一个已经不存在的目录。
        #
        #   ★ 常态路径只做几次 stat，什么都不缺就直接返回 ——
        #     **不能每轮都调 `ensure_state_dir()`**：它里面的 `_write_budget`
        #     是无条件重写，等于每轮一次文件写入。第一版就是这么干的，
        #     全量测试从 140 秒变成 3395 秒（25 倍）。
        #     自愈本身没错，错在把「补缺」和「重写」绑在了一起。
        self._heal_state_dir_if_missing()

        transitions: list[PacketTransition] = []
        errors: list[str] = []

        # 构建依赖状态缓存（一次遍历，后续所有 packet 复用）
        dep_states = {pid: p.state for pid, p in self._packets.items()}
        self._scheduling_config = load_scheduling_config(self.state_dir)
        self._ready_queue_entries = build_ready_queue(
            self._packets,
            lock_table=self._lock_table,
            dep_states=dep_states,
        )
        counts = count_packet_states(tuple(self._packets.values()))
        running_capacity = remaining_capacity("running", counts, self._scheduling_config)
        allowed_entries = (
            self._ready_queue_entries
            if running_capacity is None
            else self._ready_queue_entries[:running_capacity]
        )
        self._ready_to_start = frozenset(entry.packet_id for entry in allowed_entries)
        self._write_scheduling(Path(self.state_dir))
        ctx = ReconcileContext(
            dep_states=dep_states,
            scheduling=self._scheduling_config,
            ready_queue=tuple(entry.packet_id for entry in self._ready_queue_entries),
            ready_to_start=self._ready_to_start,
        )

        for pid in sorted(self._packets):
            packet = self._packets[pid]
            if packet.state in TERMINAL_STATES:
                continue

            try:
                t = self._reconcile_one(packet, ctx)
                if t is not None:
                    transitions.append(t)
                    self._dirty = True
                    # ★ **每个 transition 立刻落盘**，不等整轮结束。
                    #
                    #   `_try_running_to_review` 里的 `settle()` 是阻塞的：
                    #   8 个 running packet 在同一轮里顺序 settle，而落盘在
                    #   整轮之后 —— 于是磁盘状态直到**最慢的那个 worker**
                    #   结束前，一直停在 running。
                    #
                    #   2026-08-13 实测：两个 worker 21:33 就失败了，
                    #   而 8 分钟后磁盘上仍是 8 个 running，界面上什么都看不到。
                    #
                    #   ★ 这与 `_run_until_stable` 文档里记的是同一个缺陷，
                    #     只是从「tick 粒度」下沉到了「packet 粒度」——
                    #     单 packet 时看不出来，并行起来立刻暴露。
                    self.save_state()
            except Exception as exc:
                errors.append(f"{pid} ({packet.state}): {exc}")

        # 收集 guardian 拦截记录
        for ic in (self.guardian.drain_interceptions() if self.guardian else ()):
            errors.append(f"[{ic.code}] {ic.packet_id}: {ic.detail}")

        return TickReport(transitions=tuple(transitions), errors=tuple(errors))

    def run_until_stable(self, *, max_ticks: int = 20) -> TickReport:
        """反复 tick 直到没有更多状态可推进，或达到最大轮数。

        ★ 用于测试：load_state → run_until_stable → 断言终态。
        """
        all_transitions: list[PacketTransition] = []
        all_errors: list[str] = []

        for _ in range(max_ticks):
            report = self.tick()
            if not report.transitions and not report.errors:
                break
            all_transitions.extend(report.transitions)
            all_errors.extend(report.errors)

        return TickReport(transitions=tuple(all_transitions), errors=tuple(all_errors))

    # ════════════════════════════════════════════════════════════
    #  单 packet 调和
    # ════════════════════════════════════════════════════════════

    def _reconcile_one(
        self, packet: WorkPacket, ctx: ReconcileContext
    ) -> PacketTransition | None:
        """对单个 packet 尝试推进状态。返回实际发生的转换或 None。"""

        state = packet.state

        if state == "pending":
            return self._try_pending_to_ready(packet, ctx)
        elif state == "ready":
            return self._try_ready_to_running(packet, ctx)
        elif state == "running":
            return self._try_running_to_review(packet, ctx)
        elif state == "blocked":
            return self._try_blocked_to_ready(packet, ctx)
        elif state == "review":
            return self._try_review_to_accepted(packet)

        return None

    # ── pending → ready ──────────────────────────────────────

    def _try_pending_to_ready(
        self, packet: WorkPacket, ctx: ReconcileContext
    ) -> PacketTransition | None:
        """pending → ready：所有依赖都已 accepted。"""
        unsatisfied: list[PacketId] = []
        for dep_id in packet.deps:
            dep_state = ctx.dep_states.get(dep_id)
            if dep_state != "accepted":
                unsatisfied.append(dep_id)
                # 如果依赖不在已知 packet 中，也视为未满足（外部依赖需人工确认）
                if dep_id not in self._packets:
                    unsatisfied.append(dep_id)

        if unsatisfied:
            return None  # 静默跳过 ——「还在等」不是错误

        return self._apply_transition(
            packet,
            target="ready",
            detail=f"所有 {len(packet.deps)} 个依赖均已 accepted",
            evidence_refs=(),
        )

    # ── ready → running ──────────────────────────────────────

    def _try_ready_to_running(
        self, packet: WorkPacket, ctx: ReconcileContext
    ) -> PacketTransition | None:
        """ready → running：获取路径锁，生成 worker 实例。

        两条路：
        (a) 无 WorkerRuntime → 仅获取锁，不实际派发。
            状态仍推到 running，因为没有锁就不能放别的 ready。
        (b) 有 WorkerRuntime → 获取锁 + spawn worker。
        """
        if not packet.ownsPaths:
            return self._apply_transition(
                packet, target="blocked",
                detail="ready 但 ownsPaths 为空，无写权限无法执行",
                evidence_refs=(),
            )

        config = ctx.scheduling or self._scheduling_config
        if packet.id not in ctx.ready_to_start:
            return None

        counts = count_packet_states(tuple(self._packets.values()))
        if not under_wip_limit("running", counts, config):
            return None

        # 获取路径锁
        now = _now_iso()
        # 所有权图版本使用锁表当前版本（乐观锁基础）
        result = self._lock_table.acquire(
            packet.id,
            packet.ownsPaths,
            at=now,
            expected_version=self._lock_table.version,
        )

        if not result.ok:
            # 锁冲突 —— 下轮再试（或者转为 blocked）
            return None

        # 生成系统证据
        ev = f"sys:lock:{packet.id}:{self._tick_count}"
        evidence = (EvidenceRef(ev),)

        # Guardian 拦截：I5 尝试次数检查
        if self.guardian is not None:
            ok, reason = self.guardian.check_attempts(packet)
            if not ok:
                return self._apply_transition(
                    packet, target="abandoned",
                    detail=reason,
                    evidence_refs=(),
                )

        # 全局预算检查：是否有足够余额
        if self.budget_tracker is not None:
            if not self.budget_tracker.can_afford(packet.budget.limitCny):
                # 预算不足 → blocked
                return self._apply_transition(
                    packet, target="blocked",
                    detail=(
                        f"全局预算不足：剩余 ${self.budget_tracker.remaining:.2f}，"
                        f"需要 ${packet.budget.limitCny:.2f}"
                    ),
                    evidence_refs=(),
                )

        # 如果有 WorkerRuntime，尝试 spawn
        if self.worker_runtime is not None:
            # ★ 确保持久 event loop 已创建
            # 不能用 asyncio.run() 因为 B 的 LocalWorkerRuntime 在 spawn() 中创建
            # 后台 task，必须用同一个 loop 在 settle() 中等待
            asyncio.set_event_loop(self._event_loop())
            try:
                # 异步 spawn —— reconcile 是同步循环，
                # 这里使用同步适配（由 WorkerRuntime 实现者保证 spawn 立即返回句柄）
                # 注意：settle() 在 _try_running_to_review 中异步等待。
                handle = self._event_loop().run_until_complete(
                    self.worker_runtime.spawn(
                        self._build_spawn_request(packet)
                    )
                )
                self._active_workers[packet.id] = handle
            except Exception as exc:
                # spawn 失败 —— 释放锁，回退
                #
                # ★ 这里原来只 `return None`，异常连日志都不打。后果不是崩溃，
                #   是 packet 永远停在 ready：run_until_stable 看不到任何转换，
                #   就认为系统已经"稳定"并正常退出。整条链路静默卡死，
                #   而唯一的线索是"状态没动"。
                #   实测踩过一次：repo_root 配成 worker 工作区（而不是被开发的
                #   项目仓库），B 的 WorktreeIsolationError 被吞掉，
                #   表面现象只是"跑不到 accepted"，排查方向完全被误导。
                #   本项目的主张是「可靠性来自不变量，不来自提示词」——
                #   那么失败就必须是显式的，这是同一条原则的下位要求。
                logger.warning(
                    "spawn 失败，packet %s 保持 ready 并释放锁：%s: %s",
                    packet.id, type(exc).__name__, exc,
                )
                self._lock_table.release(packet.id)
                return None

        return self._apply_transition(
            packet,
            target="running",
            detail=f"锁已获取：{', '.join(packet.ownsPaths[:3])}"
            + (f"（共 {len(packet.ownsPaths)} 条）" if len(packet.ownsPaths) > 3 else ""),
            evidence_refs=evidence,
        )

    # ── running → review / blocked ───────────────────────────

    def _try_running_to_review(
        self, packet: WorkPacket, ctx: ReconcileContext
    ) -> PacketTransition | None:
        """检查 running packet 的 worker 是否已经结束。

        没有 WorkerRuntime 时 → 跳过（无法判定是否完成，留给外部驱动）。
        """
        if self.worker_runtime is None:
            return None

        config = ctx.scheduling or self._scheduling_config
        counts = count_packet_states(tuple(self._packets.values()))
        if not under_wip_limit("review", counts, config):
            return None

        handle = self._active_workers.get(packet.id)
        if handle is None:
            # worker 句柄丢失（崩溃恢复后）→ 尝试重新接管
            return None

        try:
            outcome = self._event_loop().run_until_complete(self.worker_runtime.settle(handle))
        except Exception:
            # Worker 还在跑或出错了 —— 下轮再看
            return None

        # 释放锁（无论成败）
        self._lock_table.release(packet.id)
        self._active_workers.pop(packet.id, None)

        if hasattr(outcome, "status") and outcome.status == "completed":
            # 记录支出到全局预算
            if self.budget_tracker is not None and hasattr(outcome, "spent_cny"):
                spent = getattr(outcome, "spent_cny", 0.0)
                if spent > 0:
                    self.budget_tracker.spend(
                        spent,
                        role=packet.role,
                        model=packet.routing.model if packet.routing else "",
                    )

            # ★ 「说完成」不等于「干了活」—— 缺陷二的另一半。
            #
            #   2026-08-10 修掉的是「拿控制面自己的簿记当证据」，
            #   B 在 runner 里修掉的是「模型明说 blocker 却判 completed」。
            #   剩下这一半更安静：**模型什么都没说，只是什么也没做** ——
            #   把代码写在回复正文里，一个文件都没落，
            #   `stop_reason=end` + 无 tool_calls → runner 判 completed →
            #   证据是真的（result.json 确实存在）→ 门禁放行 → accepted。
            #
            #   判据必须从「模型说了什么」换成「工作区里多了什么」。
            #   而这个数据**一直都在**：`WorkerCompleted.touched_paths` 是冻结契约
            #   里的字段，worker 老老实实报了改过哪些文件 ——
            #   ★ 控制平面从来没看过一眼。不是缺数据，是缺判定。
            #
            #   只对「必然产生文件改动」的 kind 生效：review / spike / design
            #   本来就可能一个文件都不碰，一刀切会把它们全判失败。
            #   ★★ 第二次踩同一个坑：`touched_paths` 本身也会被簿记污染。
            #   实测（08-11 真链路）：模型一个文件都没建，`touched_paths` 却有
            #   10 条 —— 全是 `.codentum/evidence/**`，harness 自己写进工作区的
            #   prompt / response / usage / manifest。判据「改动数 > 0」被
            #   **系统自己的产物**满足了。
            #
            #   这和「拿 sys: 簿记当证据」是同一个 bug 下沉了一层：
            #   前者污染的是 evidence 列表，这里污染的是 touched_paths。
            #   单元测试全绿（mock 里 touched_paths 是干净的），
            #   ★ 只有真的跑一次链路才看得见。
            touched = _worker_authored_paths(getattr(outcome, "touched_paths", ()) or ())
            if packet.kind in MUST_TOUCH_FILES_KINDS and not touched:
                marker = EvidenceRef(
                    f"{WORKER_FAILED_EVIDENCE_PREFIX}{packet.id}:acceptance_not_met"
                )
                logger.warning(
                    "packet %s（kind=%s）自称完成，但 touched_paths 为空 —— "
                    "没有任何文件被改动，判为未完成。进入 review 但不会被自动验收。",
                    packet.id, packet.kind,
                )
                return self._apply_transition(
                    packet,
                    target="review",
                    detail=(
                        f"Worker 自称完成但未改动任何文件（kind={packet.kind}）——"
                        f"「写了字」不等于「交了活」，进入评审"
                    ),
                    evidence_refs=(tuple(outcome.evidence) or ()) + (marker,),
                    extra_updates={"attempts": packet.attempts + 1},
                )

            return self._apply_transition(
                packet,
                target="review",
                detail=f"Worker 完成，spent=¥{outcome.spent_cny:.4f}，改动 {len(touched)} 个路径",
                evidence_refs=tuple(outcome.evidence) if outcome.evidence else (),
                extra_updates={"attempts": packet.attempts + 1},
            )
        elif hasattr(outcome, "status") and outcome.status == "failed":
            # 失败也记录支出（已经花了钱）
            if self.budget_tracker is not None and hasattr(outcome, "spent_cny"):
                spent = getattr(outcome, "spent_cny", 0.0)
                if spent > 0:
                    self.budget_tracker.spend(
                        spent,
                        role=packet.role,
                        model=packet.routing.model if packet.routing else "",
                    )

            # ★ 把失败落成一条证据。原来失败只写在 detail 字符串里，
            #   _try_review_to_accepted 读不到，于是一个跑挂了的 packet
            #   照样能被兜底分支验收掉 —— 静默地把失败当成功。
            failed_marker = EvidenceRef(
                f"{WORKER_FAILED_EVIDENCE_PREFIX}{packet.id}:{outcome.reason_code}"
            )
            worker_evidence = tuple(getattr(outcome, "evidence", ()) or ())
            # ★ outcome.detail 必须带上。原来只留 reason_code，而 reason_code
            #   只有 8 个取值 —— runtime_error 这一个就能对应无数种真实原因。
            #   实测吃过亏：真因是 "no worker runner configured"（一行配置没写），
            #   但控制面只显示 runtime_error，看上去像执行环境挂了，
            #   排查方向被带偏了整整一轮。
            reason_detail = getattr(outcome, "detail", "") or ""
            logger.warning(
                "packet %s 的 worker 失败（%s: %s），进入 review 但不会被自动验收",
                packet.id, outcome.reason_code, reason_detail,
            )
            return self._apply_transition(
                packet,
                target="review",
                detail=(
                    f"Worker 失败 ({outcome.reason_code}"
                    + (f": {reason_detail}" if reason_detail else "")
                    + ")，进入评审"
                ),
                evidence_refs=worker_evidence + (failed_marker,),
                extra_updates={"attempts": packet.attempts + 1},
            )
        else:
            # aborted
            return self._apply_transition(
                packet, target="blocked",
                detail=f"Worker 被中止: {getattr(outcome, 'reason', 'unknown')}",
                evidence_refs=(),
            )

    # ── blocked → ready ──────────────────────────────────────

    def _try_blocked_to_ready(
        self, packet: WorkPacket, ctx: ReconcileContext
    ) -> PacketTransition | None:
        """blocked → ready：阻塞条件是否已解除。

        当前最简单的判据：依赖是否已满足 + 是否有可用的写路径。
        更复杂的阻塞类型（如「等待人工审批」）留到 P1。
        """
        # 如果阻塞原因是依赖未满足，现在再检查一次
        unsatisfied = [
            d for d in packet.deps
            if ctx.dep_states.get(d) != "accepted"
        ]
        if unsatisfied:
            return None  # 还在等

        return self._apply_transition(
            packet, target="ready",
            detail="阻塞条件已解除，可重新调度",
            evidence_refs=(),
        )

    # ── review → accepted / rejected ─────────────────────────

    def _integrate_before_accepting(
        self, packet: WorkPacket
    ) -> tuple[PacketTransition | None, str]:
        """验收通过后、真正标 accepted 之前，把产出合回主项目。

        ★ 「accepted」必须意味着**东西真的进了项目**，否则它只是一句状态字符串：
          packet 显示验收通过，而使用者打开项目什么都没有。

        ★ 合入失败转 **blocked** 而不是留在 review：
          留在 review 会每轮重试合并，而失败原因（工作区不干净、改了别人的路径、
          合并冲突）通常不会自己消失 —— 那样只是把一次失败变成无限次失败。

        返回 `(转移 | None, 结论)`：转移非 None 表示**已经转到 blocked**，
        调用方应直接返回它；结论无论成败都要写进 accepted 的 detail。
        """

        if self.result_integrator is None:
            return None, ""
        try:
            ok, detail = self.result_integrator(packet)
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"合入过程异常：{type(exc).__name__}: {exc}"
        if ok:
            logger.info("packet %s 合入：%s", packet.id, detail)
            # ★ 即使成功也把结论带回 accepted 的 detail —— 「没有改动」
            #   「没有执行记录」「找不到工作区」都属于「**没有合入任何东西**」，
            #   它们与「合入了 N 处」在状态上都是 accepted，
            #   不写出来就分辨不出。
            return None, detail
        logger.warning("packet %s 合入失败，转 blocked：%s", packet.id, detail)
        return self._apply_transition(
            packet, target="blocked", detail=f"验收通过但合入失败：{detail}", evidence_refs=()
        ), detail

    def _try_review_to_accepted(self, packet: WorkPacket) -> PacketTransition | None:
        """review → accepted：运行门禁判定。

        优先使用 gate_runner（若已配置），否则退回到简单证据检查。
        """
        if self.gate_runner is not None:
            # 用 gate_runner 跑门禁
            gate_id = "review"
            if self.transition_table is not None:
                # 查 TransitionTable 看需要哪个门禁
                try:
                    # ★ 用 check_system 而不是 check(role=packet.role, ...)。
                    #   后者问的是「coder 能不能触发签字」—— 而调和循环不是角色，
                    #   它在门禁通过后**代为应用**。问错了人，就把
                    #   「不能给自己签字」变成了「没有人能签字」。
                    verdict = self.transition_table.check_system(
                        packet_role=packet.role,
                        acceptance_author=packet.acceptance.authoredBy,
                        current="review",
                        target="accepted",
                        evidence=packet.evidence,
                    )
                    if not verdict.allowed:
                        return None
                    if verdict.requires_gate:
                        gate_id = verdict.requires_gate
                except Exception:
                    pass  # TransitionTable 不可用时退回到默认门禁

            gate_result = self.gate_runner.check(gate_id, packet)
            if not gate_result.passed:
                return None

            blocked, note = self._integrate_before_accepting(packet)
            if blocked is not None:
                return blocked

            return self._apply_transition(
                packet,
                target="accepted",
                detail=f"门禁 {gate_id!r} 通过：{gate_result.detail}"
                + (f"｜合入：{note}" if note else ""),
                evidence_refs=gate_result.evidence_refs,
            )

        # ── 兜底：没有配 gate_runner 时的简单证据检查 ──
        #
        # ★ 兜底仍然能验收，但判据必须是**别人给的证据**，不能是控制面
        #   自己的簿记。原来这里只判 `packet.evidence` 非空，而 packet 手上
        #   那条 `sys:lock:<pid>:<tick>` 是 _try_ready_to_running 自己写的 ——
        #   于是「拿到过锁」被当成了「活干完了」，worker 失败也照样 accepted。

        # 1) worker 明确失败过 → 任何情况下都不自动验收，留在 review 等人处理
        failed = [
            ref for ref in packet.evidence
            if ref.startswith(WORKER_FAILED_EVIDENCE_PREFIX)
        ]
        if failed:
            logger.info(
                "packet %s 有 worker 失败标记，兜底分支拒绝自动验收：%s",
                packet.id, ", ".join(failed),
            )
            return None

        # 2) 必须存在至少一条非 sys: 的真实证据
        real = tuple(ref for ref in packet.evidence if _is_acceptance_evidence(ref))
        if not real:
            logger.info(
                "packet %s 没有可验收的证据（%d 条均为控制面簿记），保持 review",
                packet.id, len(packet.evidence),
            )
            return None

        blocked, note = self._integrate_before_accepting(packet)
        if blocked is not None:
            return blocked

        return self._apply_transition(
            packet,
            target="accepted",
            detail=f"验收门禁通过（{len(real)} 条证据）" + (f"｜合入：{note}" if note else ""),
            evidence_refs=real,
        )

    # ════════════════════════════════════════════════════════════
    #  内部辅助
    # ════════════════════════════════════════════════════════════

    def _append_decision(
        self,
        packet: WorkPacket,
        from_state: PacketState,
        to_state: PacketState,
        detail: str,
    ) -> None:
        """把一次状态转移追加到 `decisions.jsonl`。

        ════════════════════════════════════════════════════════
         ★ 这条日志的契约冻结于 2026-08-02，而从来没有一处写过它
        ════════════════════════════════════════════════════════

        `DecisionRecord` 定义完整、schema 冻结、被 `__init__` 导出 ——
        但**全仓库没有任何一处构造过它**。`decisions.jsonl` 被
        `ensure_state_dir()` 建成空文件，然后一直是空的。

        后果具体而且致命：**每个 packet 在每个状态停了多久，这段历史根本不存在。**
        于是流动效率、等待 p80、瓶颈这些都算不出来 ——
        不是算法没写，是**没有可算的东西**。

        ★ 挂在 `_apply_transition` 上，因为那是 reconcile **唯一**修改状态的地方。
          挂在别处必然漏。

        ════════════════════════════════════════════════════════
         ★ actor 字段的一处取舍（契约缺口，记在案）
        ════════════════════════════════════════════════════════

        `DecisionRecord.actor` 只允许 `RoleId | "operator"` ——
        **没有「控制平面自己」这个取值**，而状态转移恰恰是控制平面决定的。

        这里填 packet 的角色（读作「这个角色的活动了」），
        真正的决策者放进机器可读的 `reasonCode`（`Reconcile.*`）。

        不改契约是因为它冻结了（I3），改它要三人同意 + 新 ADR + 变更窗口；
        而这个缺口不影响任何判定 —— 但它是**真实的建模缺口**，不该假装没有。
        """

        try:
            record = DecisionRecord(
                at=_now_iso(),
                actor=packet.role,
                action="packet_transitioned",
                packetId=packet.id,
                reasonCode=f"Reconcile.{from_state}_to_{to_state}",
                detail=detail or None,
            )
            path = Path(self.state_dir) / "decisions.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(record.model_dump_json(exclude_none=True) + "\n")
        except OSError as exc:
            # ★ 记账失败不能拖垮状态推进 —— 但也不能静默：
            #   日志悄悄停止增长，会让 flow.json 上的数字变成假数，
            #   而那比没有 flow.json 更糟。
            logger.warning("决策日志写入失败（状态推进不受影响）：%s", exc)

    def _apply_transition(
        self,
        packet: WorkPacket,
        *,
        target: PacketState,
        detail: str,
        evidence_refs: Sequence[EvidenceRef],
        extra_updates: dict[str, object] | None = None,
    ) -> PacketTransition:
        """原子地更新 packet 状态并返回转换记录。

        ★ 这就是 reconcile 唯一修改状态的地方。
        """
        old_state = packet.state

        updates: dict[str, object] = {"state": target}
        if evidence_refs:
            updates["evidence"] = tuple(evidence_refs)
        if extra_updates:
            updates.update(extra_updates)

        new_packet = packet.model_copy(update=updates)
        self._packets[packet.id] = new_packet

        self._append_decision(packet, old_state, target, detail)

        return PacketTransition(
            packet_id=packet.id,
            from_state=old_state,
            to_state=target,
            detail=detail,
        )

    def _build_spawn_request(self, packet: WorkPacket) -> SpawnRequest:
        """从 WorkPacket 构造 SpawnRequest。

        ★ 控制平面不接触 RoleSpec —— tools 留空，
          WorkerRuntime 实现负责从 RoleSpec 派生工具面。
        """
        mounts: list[MountSpec] = []

        # 写权限路径
        for p in packet.ownsPaths:
            mounts.append(MountSpec(host_path=p, mount_path=p, mode="rw"))

        # 只读挂载路径
        for p in packet.readsPaths:
            mounts.append(MountSpec(host_path=p, mount_path=p, mode="ro"))

        return SpawnRequest(
            packet_id=packet.id,
            role=packet.role,
            mounts=tuple(mounts),
            tools=(),  # ★ WorkerRuntime 负责填
            routing=packet.routing if packet.routing else ModelRouting(model="default", effort="medium"),
            budget=BudgetGrantRuntime(
                limit_cny=packet.budget.limitCny,
                degradation_chain=packet.budget.degradationChain,
            ),
            workspace=str(
                Path(self.state_dir).parent.parent / "codentum-workers" / packet.id / f"attempt-{packet.attempts + 1}"
            ),
            attempt=packet.attempts + 1,
        )

    # ════════════════════════════════════════════════════════════
    #  查询（给测试和桌面端用）
    # ════════════════════════════════════════════════════════════

    @property
    def packets(self) -> Mapping[PacketId, WorkPacket]:
        return self._packets

    @property
    def packet_count(self) -> int:
        return len(self._packets)

    def packet(self, pid: PacketId) -> WorkPacket:
        return self._packets[pid]
