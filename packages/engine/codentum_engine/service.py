"""引擎服务 —— 把 delivery 协议接到 ReconcileLoop 上。

════════════════════════════════════════════════════════════════
 这个模块补的是哪个洞
════════════════════════════════════════════════════════════════

在它之前，链路是断的：

    App.tsx → IPC → PythonEngineClient → SidecarGateway → JsonlEngineProxy
                                                              ↓
                                                    ★ 这里没有引擎 ★
    ReconcileLoop（能跑真 worker，但只被测试调用过）

`engine_proxy` 唯一对话过的「引擎」是 `packages/delivery/tests/_fake_engine.py`，
而那个文件自己的第一行就写着 "never shipped as an engine"。

这个缺口落在三个人的边界之间：C 侧从 UI 到 Python 协议全部接完，A 的控制
平面也能跑，但**没有一个进程把两头接起来**，`boundaries.yaml` 里三个人的
responsibility 也都没写它。


════════════════════════════════════════════════════════════════
 三个不能照抄假引擎的地方
════════════════════════════════════════════════════════════════

**一、能力表必须说真话。**
假引擎把 9 个 capability 全报 true。真引擎照抄的话，桌面端会把「暂停」
「从检查点分叉」这些按钮显示为可用，点下去什么都不会发生 —— 这正是 C 在
任务书里立的规矩「未接入的插件或 Skill 返回不可用，不得显示为已启用」的
反面。这里只报实现了的那几项，其余一律 false。

**二、`submit_requirement` 不能返回 `applied`。**
网关的默认请求超时是 8 秒（`gateway.py`），而一个真实模型 packet 要跑
30～180 秒。假引擎立刻回 `applied` 是因为它什么也没做。真引擎必须回
`accepted`（协议里本来就有这个状态），把执行放到后台线程，让桌面端通过
监视 `.codentum/` 看进展。回 `applied` 等于在结果出来之前就宣布结果。

**三、四个安全组件必须显式打开。**
`ReconcileLoop` 的 `transition_table` / `gate_runner` / `budget_tracker` /
`guardian` **默认全是 None** —— 这对一个库是合理的默认，对一个产品入口不是。
08-10 的护栏消融实验量到的数字：关掉之后 I1 冲突 8/8 全部放行、重试上限
8/8 全部放行、全局预算 8/8 全部放行。**默认值的选择权在入口这一层，
这里就是那一层。**
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from codentum_contracts.state import RoleSpec, WorkPacket
from codentum_control_plane.admission import AdmissionChecker
from codentum_control_plane.budget import BudgetTracker
from codentum_control_plane.gates import GateRunner, register_builtin_gates
from codentum_control_plane.guardian import Guardian
from codentum_control_plane.reconcile import ReconcileLoop
from codentum_control_plane.state_machine import TransitionTable
from codentum_delivery.protocol import CAPABILITY_NAMES, PROTOCOL_VERSION, JsonValue
from codentum_harness.context_broker import ContextCandidate
from codentum_harness.runtime import (
    LocalWorkerRuntimeConfig,
    ModelGatewayConfig,
    RunnerConfig,
    build_local_worker_runtime,
)
from codentum_harness.worker import LocalWorkerRuntime
from codentum_roles.loader import load_builtin_role_specs

from .intake import (
    DEFAULT_PACKET_BUDGET_CNY,
    RequirementRecord,
    RequirementStore,
    build_packet_for_requirement,
    choose_acceptance_author,
    new_packet_id,
)
from .session import EngineSession

__all__ = ["ENGINE_VERSION", "EngineConfig", "EngineService"]

logger = logging.getLogger(__name__)

ENGINE_VERSION = "codentum-engine/0.1.0"

_KEY_ENVS = ("DASHSCOPE_API_KEY", "BAILIAN_API_KEY", "QWEN_API_KEY", "AGENTTEAMS_LLM_API_KEY")

# ★ 目前真正实现了的动作只有一个。其余八个不是「以后再说」，是「现在按下去
#   不会发生任何事」—— 报 false 之后网关会直接以 capability_unavailable 拒绝，
#   桌面端也就不会把它们显示成可用。
#
#   加实现的时候记得同步这里；忘了同步的后果是「实现了但用不了」，
#   比「没实现」更难查。
IMPLEMENTED_CAPABILITIES: tuple[str, ...] = ("requirements",)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _resolve_key_env(explicit: str | None) -> str | None:
    """找到一个**确实有值**的 Key 环境变量名。

    ★ 只看变量名存不存在是不够的：`DASHSCOPE_API_KEY=` 这种空值会让
      handshake 报「能力可用」，而真跑起来在 provider 那边才炸。
    """

    candidates = (explicit,) if explicit else _KEY_ENVS
    for name in candidates:
        if name and os.environ.get(name, "").strip():
            return name
    return None


@dataclass
class EngineConfig:
    """引擎启动参数。

    ★ 每一项都可从命令行/环境变量给出，没有一项从代码里猜。
    """

    project_root: Path
    state_dir: Path | None = None

    model: str = "qwen-coder-plus-1106"
    "★ coder RoleSpec 的 modelPolicy.defaultModel，08-09 经 B 实测可用。"

    effort: str = "medium"

    global_budget_cny: float = 5.0
    "全局预算上限。跑真模型要花钱，这个数必须是人给的，不能有默认的『无限』。"

    packet_budget_cny: float = DEFAULT_PACKET_BUDGET_CNY

    api_key_env: str | None = None
    "指定用哪个环境变量取 Key；为 None 时按 _KEY_ENVS 顺序找第一个有值的。"

    model_timeout_seconds: float = 180.0

    context_char_budget: int = 8000

    enforce_role_transitions: bool = False
    """是否把 RoleSpec 派生的 TransitionTable 装进 ReconcileLoop。

    ★ 默认 False，而且这个默认值需要解释 —— 因为它看起来像是在关护栏。

    打开它会让 coder 的 packet **永远停在 review**：`_try_review_to_accepted`
    用 `role=packet.role` 去查表，而 `review → accepted` 只有 reviewer 声明了
    （coder 只声明了 running→review / running→blocked）。也就是说表是对的
    ——「coder 不能给自己的活签字」正是想要的语义 —— 但 reconcile 问表的
    方式（拿 packet 自己的 role 去问）让这条规则等价于「没有人能签字」。

    这是 A 自己模块里的一处建模问题，不是配置问题，**不应该在入口层绕过**。
    `tests/test_role_transition_gap.py` 用一条测试把两个分支都钉住了：
    打开会停在 review，关掉才会走完。修好之后那条测试会变红。
    """

    def resolved_state_dir(self) -> Path:
        return self.state_dir if self.state_dir is not None else self.project_root / ".codentum"


@dataclass
class EngineService:
    """协议方法的实现体。**不碰 stdio** —— 那是 `__main__` 的事。

    分开是为了让它可测：测试直接调 `handshake()` / `command()`，
    不需要起子进程、不需要拼 JSONL。
    """

    config: EngineConfig

    _session: EngineSession = field(init=False)
    _role_specs: tuple[RoleSpec, ...] = field(init=False)
    _requirements: RequirementStore = field(init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _workers: list[threading.Thread] = field(default_factory=list, init=False)
    _key_env: str | None = field(init=False)
    _stopped: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        state_dir = self.config.resolved_state_dir()
        state_dir.mkdir(parents=True, exist_ok=True)
        self._session = EngineSession.load_or_create(state_dir)
        self._role_specs = load_builtin_role_specs()
        self._requirements = RequirementStore(state_dir)
        self._key_env = _resolve_key_env(self.config.api_key_env)

    # ══════════════════════════════════════════════════════════
    #  协议方法
    # ══════════════════════════════════════════════════════════

    def handshake(self) -> dict[str, JsonValue]:
        """★ 能力表按「现在按下去会不会真的发生事情」来报，不按路线图报。"""

        # 没有 Key 就没有 runner，没有 runner 的话 packet 建出来也不会被执行 ——
        # 那是最难查的一种「成功」：命令被接受了，状态却永远不动。
        # 与其如此，不如在能力表上就说不可用。
        usable = self._key_env is not None
        capabilities: dict[str, JsonValue] = {
            name: (name in IMPLEMENTED_CAPABILITIES and usable) for name in CAPABILITY_NAMES
        }
        handshake: dict[str, JsonValue] = {
            "connected": True,
            "protocolVersion": PROTOCOL_VERSION,
            "engineVersion": ENGINE_VERSION,
            "stateRevision": self._session.revision,
            "runId": self._session.run_id,
            "projectRoot": str(self.config.project_root.resolve()),
            "capabilities": capabilities,
        }
        if not usable:
            handshake["unavailableReason"] = (
                "未配置模型 Key（" + " / ".join(_KEY_ENVS) + " 任一），"
                "需求提交会被创建但永远不会执行，因此报为不可用"
            )
        return handshake

    def command(self, command: Mapping[str, Any]) -> dict[str, JsonValue]:
        """处理一条 OperatorCommand，返回 CommandReceipt。

        ★ 网关已经做过结构校验、幂等、能力、revision 和 projectRoot 的检查
          （`gateway.py`），这里不重复做那些。这里只做网关做不了的：
          它不知道这个 action 在引擎内部意味着什么。
        """

        command_id = str(command.get("commandId", ""))
        action = command.get("action")

        if self._stopped:
            return self._receipt(command_id, "rejected", reason="engine_stopping")

        if action == "submit_requirement":
            return self._submit_requirement(command_id, command)

        # ★ 走到这里说明网关的能力检查放行了一个我们没实现的动作 ——
        #   要么是能力表写错了，要么是网关被绕过了。两种都该说清楚，
        #   不该静默回一个 applied。
        return self._receipt(
            command_id,
            "rejected",
            reason=f"action_not_implemented:{action}",
        )

    def shutdown(self, *, join_timeout: float = 30.0) -> dict[str, JsonValue]:
        """停止接收新命令，并等在跑的 packet 收尾。

        ★ 不强杀。正在跑的 worker 手上可能握着路径锁，
          进程被打断而锁没释放的话，下次启动会看到一把没有主人的锁。
        """

        self._stopped = True
        pending = [t for t in self._workers if t.is_alive()]
        for thread in pending:
            thread.join(timeout=join_timeout)
        still_running = [t for t in pending if t.is_alive()]
        return {
            "stopped": True,
            "pendingWorkers": len(still_running),
        }

    # ══════════════════════════════════════════════════════════
    #  submit_requirement
    # ══════════════════════════════════════════════════════════

    def _submit_requirement(
        self, command_id: str, command: Mapping[str, Any]
    ) -> dict[str, JsonValue]:
        payload = command.get("payload")
        if not isinstance(payload, dict):
            return self._receipt(command_id, "rejected", reason="payload_must_be_object")

        requirement = payload.get("requirement") or payload.get("text")
        if not isinstance(requirement, str) or not requirement.strip():
            # ★ 这条拒绝是有意义的，不是防御性代码：08-10 真实模型跑通时，
            #   模型收到的 prompt 里一句任务描述都没有，回了份 blocker 报告。
            #   与其把一个没有需求正文的 packet 建出来再让模型去猜，
            #   不如在入口就说清楚缺什么。
            return self._receipt(command_id, "rejected", reason="requirement_text_is_empty")

        if self._key_env is None:
            return self._receipt(command_id, "rejected", reason="capability_unavailable:requirements")

        packet_id = new_packet_id()
        owns = _string_list(payload.get("ownsPaths")) or ("workspace/",)
        reads = _string_list(payload.get("readsPaths"))
        budget = payload.get("budgetCny")
        packet_budget = (
            float(budget)
            if isinstance(budget, (int, float)) and not isinstance(budget, bool) and budget > 0
            else self.config.packet_budget_cny
        )

        packet = build_packet_for_requirement(
            packet_id=packet_id,
            requirement=requirement,
            owns_paths=owns,
            reads_paths=reads,
            model=self.config.model,
            effort=self.config.effort,
            budget_cny=packet_budget,
            acceptance_author=choose_acceptance_author(
                [str(spec.id) for spec in self._role_specs], packet_role="coder"
            ),
        )

        # ★ 准入校验在写盘之前跑。写完再校验等于「先污染再检查」——
        #   一个违规 packet 一旦落进 packets/，下次 load_state 就会把它读回来。
        verdict = AdmissionChecker(role_specs=self._role_specs).check(packet)
        if not verdict:
            codes = ",".join(v.code for v in verdict.violations)
            logger.warning("packet %s 未通过准入：%s", packet_id, codes)
            return self._receipt(command_id, "rejected", reason=f"admission_rejected:{codes}"[:512])

        self._requirements.save(
            RequirementRecord(
                packet_id=str(packet_id),
                text=requirement,
                submitted_at=_now_iso(),
                command_id=command_id,
                payload=dict(payload),
            )
        )

        with self._lock:
            loop = self._build_loop()
            loop.load_state()
            loop._packets[packet.id] = packet  # noqa: SLF001 —— 见下方注释
            loop._dirty = True  # noqa: SLF001
            loop.save_state()
            revision = self._session.bump()

        # ★ 直接写 loop 的私有字段是有意的，也是一处**记在案的欠账**：
        #   ReconcileLoop 目前没有公开的 `admit(packet)` 入口，
        #   现有调用方（测试）都是先把 packet 文件写进 packets/ 再 load_state。
        #   在这里复刻那套「先写文件再重读」会让准入校验和落盘之间出现一个
        #   窗口。正确的修法是给 ReconcileLoop 加一个公开的准入方法 ——
        #   那是 A 的控制平面改动，不该顺手塞进这次接线里。
        #   记为待办：`docs/项目进展与记忆.md` 待办 27。

        thread = threading.Thread(
            target=self._run_until_stable,
            name=f"engine-reconcile-{packet_id}",
            daemon=True,
        )
        self._workers.append(thread)
        thread.start()

        # ★ accepted 而不是 applied：命令收下了，活还没干完。
        #   干完了没有由 `.codentum/` 里的 packet 状态说了算，不由这条回执说。
        return self._receipt(command_id, "accepted", revision=revision)

    def _run_until_stable(self, *, max_ticks: int = 30) -> None:
        """后台把状态推到稳定。**每一轮都落盘。**

        ★ 这里没有直接用 `loop.run_until_stable()`，理由不是风格偏好：
          那个方法内部连跑最多 30 轮，**只在最外层保存一次**。而一次真实
          模型调用要 30～240 秒，全程 `.codentum/` 里的 packet 一直是
          `pending`。

          后果是桌面端在整段执行期间显示「等待中」，直到最后一刻突然跳到
          `accepted` —— 一个「看得见执行过程」的产品，恰好在执行过程中
          什么都看不见。2026-08-11 首次真机跑通时实测：模型 09:57 开始，
          10:01 返回，中间 4 分钟磁盘状态零变化。

          `run_until_stable` 本身没错，它是给测试用的（load → 跑完 → 断言
          终态）。**产品入口需要的是另一种刷新策略**，而选择刷新策略正是
          装配点的职责。

        ★ 任何异常都不能让线程静默死掉 —— 那样桌面端看到的现象是
          「packet 永远停在 pending」，且没有任何线索。
        """

        try:
            logger.info("后台 reconcile 启动")
            with self._lock:
                loop = self._build_loop()
                loop.load_state()
                # ★ 这条 info 留着不是凑数：装配一次要跑 git 子进程，
                #   2026-08-11 那个「卡 240 秒」的缺陷就是靠它与上一条的
                #   时间差定位的。装配看起来不像会做 I/O 的事，所以更需要打点。
                logger.info("装配完成，开始 tick")
                for _ in range(max_ticks):
                    report = loop.tick()
                    # ★ 先落盘再记日志：日志丢了只是少条线索，
                    #   状态没落盘则是桌面端看到假象。
                    loop.save_state()
                    if report.transitions:
                        self._session.bump()
                    for transition in report.transitions:
                        logger.info(
                            "%s: %s → %s（%s）",
                            transition.packet_id,
                            transition.from_state,
                            transition.to_state,
                            transition.detail,
                        )
                    for error in report.errors:
                        logger.warning("reconcile 错误：%s", error)
                    if not report.transitions and not report.errors:
                        break
        except Exception:
            logger.exception("后台 reconcile 失败")

    # ══════════════════════════════════════════════════════════
    #  装配
    # ══════════════════════════════════════════════════════════

    def _build_loop(self) -> ReconcileLoop:
        """★ 四个安全组件在这里显式打开 —— 见模块头「三」。"""

        gate_runner = GateRunner()
        register_builtin_gates(gate_runner)

        loop = ReconcileLoop(
            state_dir=str(self.config.resolved_state_dir()),
            gate_runner=gate_runner,
            budget_tracker=BudgetTracker(limit_cny=self.config.global_budget_cny),
            guardian=Guardian(),
            transition_table=self._transition_table(),
        )
        loop.worker_runtime = self._build_worker_runtime()
        return loop

    def _transition_table(self) -> TransitionTable | None:
        if not self.config.enforce_role_transitions:
            return None
        return TransitionTable(self._role_specs)

    def _build_worker_runtime(self) -> LocalWorkerRuntime | None:
        if self._key_env is None:
            return None
        return build_local_worker_runtime(
            LocalWorkerRuntimeConfig(
                repo_root=self.config.project_root,
                runner=RunnerConfig.model_gateway(
                    ModelGatewayConfig.bailian(
                        pricing={},
                        api_key_env=self._key_env,
                        # ★ 价格表证据还没落地（待办 7d）。显式放行 unknown
                        #   pricing，代价是**成本数字不能当证据** —— 桌面端的
                        #   Cost 视图会显示 0，那是「不知道」不是「没花钱」。
                        #   偷偷塞个假价格会让那个 0 变成一个看起来可信的数。
                        require_pricing=False,
                    ),
                    timeout_seconds=self.config.model_timeout_seconds,
                ),
                context_char_budget=self.config.context_char_budget,
            ),
            role_specs=self._role_specs,
            context_loader=self._context_loader,
        )

    def _context_loader(self, request: Any, role_spec: RoleSpec) -> tuple[ContextCandidate, ...]:
        """把需求原文送进模型上下文 —— 08-10 那个缺陷的可运行性修复。

        ★ `required=True`：这不是「有更好」的补充材料，是任务本身。
          被 char_budget 裁掉的话，模型又会收到一份没有任务的 prompt。
        """

        text = self._requirements.text_for(str(request.packet_id))
        if not text:
            return ()
        return (
            ContextCandidate(
                ref=f"requirement:{request.packet_id}",
                artifact_path=f"requirements/{request.packet_id}.json",
                text=text,
                required=True,
                summary="操作者提交的需求原文",
                priority=0,
            ),
        )

    # ══════════════════════════════════════════════════════════

    def _receipt(
        self,
        command_id: str,
        status: str,
        *,
        revision: int | None = None,
        reason: str | None = None,
    ) -> dict[str, JsonValue]:
        receipt: dict[str, JsonValue] = {
            "commandId": command_id,
            "status": status,
            "stateRevision": self._session.revision if revision is None else revision,
            "receivedAt": _now_iso(),
        }
        if reason is not None:
            receipt["reason"] = reason[:512]
        return receipt

    # 给测试用的只读视图
    @property
    def run_id(self) -> str:
        return self._session.run_id

    @property
    def revision(self) -> int:
        return self._session.revision

    def packets(self) -> Mapping[str, WorkPacket]:
        loop = ReconcileLoop(state_dir=str(self.config.resolved_state_dir()))
        loop.load_state()
        return {str(k): v for k, v in loop.packets.items()}


def _string_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item.strip())
