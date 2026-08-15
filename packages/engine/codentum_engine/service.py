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

import json
import logging
import os
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from codentum_contracts.state import ModelRouting, PacketId, RoleSpec, WorkPacket, dump_state
from codentum_control_plane.admission import AdmissionChecker
from codentum_control_plane.budget import BudgetTracker
from codentum_control_plane.gates import GateRunner, register_builtin_gates
from codentum_control_plane.guardian import Guardian
from codentum_control_plane.reconcile import ReconcileLoop
from codentum_control_plane.state_machine import TransitionTable
from codentum_delivery.protocol import CAPABILITY_NAMES, PROTOCOL_VERSION, JsonValue
from codentum_harness.context_broker import ContextCandidate
from codentum_harness.evolution import experience_context_candidates_now
from codentum_harness.memory_index import (
    PersistentMemoryIndex,
    ResourceSelectionError,
    index_knowledge_sources_now,
    knowledge_sources_from_payload,
    memory_context_candidates_now,
)
from codentum_harness.model_gateway import audited_bailian_pricing
from codentum_contracts.interfaces import ModelMessage, ModelRequest
from codentum_harness.runtime import (
    LocalWorkerRuntimeConfig,
    ModelGatewayConfig,
    RunnerConfig,
    TokenPricingConfig,
    build_local_worker_runtime,
    build_model_gateway,
)
from codentum_harness.worker import LocalWorkerRuntime, ensure_project_initialized
from codentum_roles.loader import (
    RoleMcpLoadError,
    RoleSkillLoadError,
    load_builtin_role_specs,
    project_mcp_services,
    project_role_skills,
)

from .acceptance import build_executing_acceptance_gate
from .mcp_client import describe_skipped
from .mcp_toolbox import McpToolbox, build_mcp_toolbox
from .planner import PlannedTask, build_packets_from_plan, parse_plan, plan_prompt
from .agent_runner import DEFAULT_MAX_TURNS, AgentRunnerConfig, build_agent_runner
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

    mcp_config_dir: Path | None = None
    """MCP 配置目录（如 packages/roles/mcp/）。为 None 时不接任何 MCP。

    ★ 默认 None 是**有意的**，不是忘了接：目录里那几个可启动的 server
      靠 npx 拉起来，启动要几秒、要网络、缺凭据的还会失败。让它默认开，
      等于给每次引擎启动加上一段不可控的等待与失败面。

    ★ 但「默认关」不等于「没接」—— 区别在这里：在此之前
      `mcp_config_dir` **全仓库无人传**，也就是根本没有办法把它打开。
      那是死代码；现在它是一个开关。连接结果写到
      `.codentum/mcp/connections.json`，开没开、连上几个、
      没连上的缺什么，都看得见。
    """

    enable_planner: bool = True
    """是否把需求拆成多个 packet。

    ★ 默认 True —— 这是「多 Agent 协同」的落点。设为 False 会退回
      一个需求一个 coder packet：产出上限是「一个模块」。

    ★ 拆解失败时**自动降级为单 packet 并出声**，不会让整个需求失败。
    """

    enable_tool_loop: bool = True
    """是否使用带工具循环的 runner（模型能真的写文件）。

    ★ 默认 True —— 这是产品要的行为。设成 False 会退回 B 的 one-shot
      `ModelGatewayRunner`：模型只能把代码写在回复正文里，一个文件都不会创建。

    ★ 两个 runner 都留着，因为它们守的判据不同：
      one-shot 守「模型能被真实调用、用量能落盘」，
      工具循环守「模型能真的交付文件」。合并成一个会丢掉前一条。
    """

    max_tool_turns: int = DEFAULT_MAX_TURNS
    """工具循环的轮数上限。撞上限时如实报 max_turns_exhausted，不伪装成完成。"""

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
    _mcp: McpToolbox | None = field(default=None, init=False)
    _project_init: object = field(default=None, init=False)
    _stopped: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        state_dir = self.config.resolved_state_dir()
        state_dir.mkdir(parents=True, exist_ok=True)
        self._session = EngineSession.load_or_create(state_dir)
        # ★ 启动就把 `.codentum/` 铺完整，不能等到第一次 save_state()。
        #
        #   `EngineSession` 为了放 engine-session.json 会先把目录建出来，
        #   于是在第一次提交需求之前，桌面端看到的是一个**存在但残缺**的
        #   状态目录 —— 界面上排开五条
        #   `[missing] Required state file is missing: ...`。
        #
        #   ★ 这比「目录根本不存在」更糟：不存在时桌面端显示「尚未初始化」，
        #     残缺时它显示的是一串错误。2026-08-11 实机第一次打开项目就撞上了，
        #     而且**是引擎引入的回归** —— 在它之前新项目压根没有 .codentum/。
        # ★ 项目必须可被 worktree 隔离使用，否则每个 packet 都会卡在 ready。
        #
        #   桌面端最常见的用法就是「打开一个新文件夹 → 提一个需求」，
        #   而刚 git init 的仓库没有 HEAD，`git worktree add` 必然失败。
        #   失败之后调和循环看不到任何转换、认为系统已稳定并正常退出 ——
        #   **使用者只看到「什么都没发生」**。
        #
        #   ★ 这一步只在「不是 git 仓库」或「一个提交都没有」时动手；
        #     已有历史的仓库一律不碰（那是别人的历史）。
        #     做没做、做了什么，都写进 handshake 与日志。
        self._project_init = ensure_project_initialized(self.config.project_root)
        if self._project_init.changed:
            logger.info("项目初始化：%s", self._project_init.detail)

        self._role_specs = load_builtin_role_specs()
        self._requirements = RequirementStore(state_dir)
        self._key_env = _resolve_key_env(self.config.api_key_env)
        # ★ 用一个**光杆** ReconcileLoop 铺目录，不要用 _build_loop()。
        #
        #   第一版写的是 `self._build_loop().ensure_state_dir()` —— 为了建几个
        #   空文件，把整个 WorkerRuntime 也构造了一遍，而那里面有
        #   `GitWorktreeManager.__init__` → `git rev-parse` 子进程。
        #
        #   后果是把「铺目录」这件必然成功的小事，绑上了「项目必须是 git 仓库、
        #   git 必须在 PATH 上、子进程必须能起来」三个前提。任何一个不成立，
        #   **引擎在 __post_init__ 里就抛异常、进程直接死**，而 sidecar 那边
        #   看到的只有一句 "A/B engine handshake failed" ——
        #   `JsonlEngineProxy._drain_stderr` 是有意丢弃 stderr 文本的
        #   （只数字节数），真因就此消失。
        #
        #   铺目录只需要知道 state_dir。别让它依赖任何可能失败的东西。
        ReconcileLoop(
            state_dir=str(state_dir),
            budget_tracker=BudgetTracker(limit_cny=self.config.global_budget_cny),
        ).ensure_state_dir()
        self._project_role_specs(state_dir)
        self._project_shared_skills(state_dir)
        self._project_mcp_services(state_dir)

    def _project_role_specs(self, state_dir: Path) -> None:
        """把 B 的 RoleSpec 投影进 `<project>/.codentum/roles/`。

        ════════════════════════════════════════════════════════════
         ★ 桌面端读的是项目里的投影，不是 packages/roles/specs/
        ════════════════════════════════════════════════════════════

        `directory-state-source.ts:262` 读的是 `roles/` 这个**项目内目录**，
        而 B 的真源在 `packages/roles/specs/*.json`。两者之间原本没有任何人搬运，
        于是桌面端「研发团队」页显示的是 C 自己维护的一份静态岗位清单 ——
        名字对得上，但**不代表这 11 个角色真的被系统加载了**
        （截图上「系统岗位 11、项目投影 0」说的就是这件事）。

        ★ 这个搬运归装配点：它是唯一同时知道「RoleSpec 从哪来」
          和「状态目录在哪」的地方。控制平面不接触 RoleSpec，
          桌面端不该去读别的包的源码目录。

        ★ 与 `ensure_state_dir()` 的「只补缺不覆盖」不同，这里**每次启动都重写**：
          投影的语义是「真源的副本」，留着旧副本比缺副本更糟 ——
          B 改了 RoleSpec 而项目里还是上一版，桌面端会显示一份**看起来正确的
          过时事实**，而没有任何东西会报错。
        """

        roles_dir = state_dir / "roles"
        try:
            roles_dir.mkdir(parents=True, exist_ok=True)
            for spec in self._role_specs:
                payload = dump_state(spec)
                (roles_dir / f"{spec.id}.json").write_text(
                    json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
            logger.info("已投影 %d 份 RoleSpec 到 %s", len(self._role_specs), roles_dir)
        except OSError as exc:
            # ★ 投影失败不该拖垮引擎：桌面端少一块展示，比整个引擎起不来轻得多。
            #   但必须留话 —— 否则「为什么研发团队页是空的」又会变成查不到的问题。
            logger.warning("RoleSpec 投影失败（%s），研发团队页会显示 0 份项目投影", exc)

    def _project_shared_skills(self, state_dir: Path) -> None:
        """把 B 的内置 Skills 投影进项目级共享空间。

        RoleSpec 里的 `skills` 只是引用关系；Worker 真正执行时需要读到 `SKILL.md`
        正文。把引用过的内置 Skill 投到 `.codentum/skills/shared/` 后，Local 与
        Team Worker 都能从同一个项目空间读取，C 的 Skills 面板也不必依赖源码目录。
        """

        skill_ids = sorted(
            {
                skill.id
                for spec in self._role_specs
                for skill in (spec.skills or ())
            }
        )
        if not skill_ids:
            return

        shared_dir = state_dir / "skills" / "shared"
        try:
            written = project_role_skills(skill_ids, shared_dir)
            logger.info("已投影 %d 个共享 Skill 文件到 %s", len(written), shared_dir)
        except (OSError, RoleSkillLoadError) as exc:
            logger.warning("Skill 共享空间投影失败（%s），Worker 将回退到内置 Skill 源", exc)

    def _project_mcp_services(self, state_dir: Path) -> None:
        """把 B 的 MCP 服务清单投影进项目状态。

        这里投影的是“运行时知道哪些 MCP 服务/工具入口”，不是“所有工具都已可调用”。
        每个服务自己的 status/authentication/error 必须说真话，桌面端只展示这份
        投影，不自行猜连接状态。
        """

        mcp_dir = state_dir / "mcp"
        try:
            written = project_mcp_services(mcp_dir)
            logger.info("已投影 %d 个 MCP 服务文件到 %s", len(written), mcp_dir)
        except (OSError, RoleMcpLoadError) as exc:
            logger.warning("MCP 服务投影失败（%s），MCP 页面会显示未接入", exc)

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
            if isinstance(budget, int | float) and not isinstance(budget, bool) and budget > 0
            else self.config.packet_budget_cny
        )

        packets = self._plan_or_single(
            requirement=requirement,
            packet_id=packet_id,
            owns=owns,
            reads=reads,
            budget_cny=packet_budget,
        )

        # ★ 准入校验在写盘之前跑。写完再校验等于「先污染再检查」——
        #   一个违规 packet 一旦落进 packets/，下次 load_state 就会把它读回来。
        checker = AdmissionChecker(role_specs=self._role_specs, recorder=self._record_judgement)
        for candidate in packets:
            verdict = checker.check(candidate)
            if not verdict:
                codes = ",".join(v.code for v in verdict.violations)
                logger.warning("packet %s 未通过准入：%s", candidate.id, codes)
                return self._receipt(
                    command_id, "rejected", reason=f"admission_rejected:{codes}"[:512]
                )

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
            # ★ 走公开的 admit()，不再直接写 loop 的私有字段（待办 27 已修）。
            #   admit 只负责纳入，准入校验由上面那几行负责 —— 顺序不能反：
            #   先校验再纳入，否则违规 packet 会先进内存再被落盘。
            for candidate in packets:
                loop.admit(candidate)
            loop.save_state()
            revision = self._session.bump()

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
        # ★ 覆盖内置的 acceptance 门禁：内置那个只检查「有没有证据」，
        #   注释里写的「完整版：实际运行验收测试」一直没写。
        #   装配点在这里把会真的执行谓词的那个装上去。
        executing_gate = build_executing_acceptance_gate(self._workers_root())
        # ★ 必须同时注册到 "review" 上。
        #
        #   `_try_review_to_accepted` 在没有 transition_table 时，用的 gate_id
        #   是 **"review"** 而不是 "acceptance"。只注册 acceptance 的话，
        #   这个会执行谓词的门禁**永远不会被调用** ——
        #   2026-08-12 实测：日志写「门禁 'review' 通过」，packet 被 accepted，
        #   而验收谓词一次都没跑过。
        #
        #   ★ 判据装了但没接上，比没装更糟：它让人以为已经在判了。
        for gate_id in ("acceptance", "review"):
            gate_runner.register(gate_id, executing_gate)

        loop = ReconcileLoop(
            state_dir=str(self.config.resolved_state_dir()),
            gate_runner=gate_runner,
            budget_tracker=BudgetTracker(limit_cny=self.config.global_budget_cny),
            guardian=Guardian(),
            transition_table=self._transition_table(),
        )
        loop.worker_runtime = self._build_worker_runtime()
        return loop

    def _plan_or_single(
        self,
        *,
        requirement: str,
        packet_id: PacketId,
        owns: tuple[str, ...],
        reads: tuple[str, ...],
        budget_cny: float,
    ) -> tuple[WorkPacket, ...]:
        """走 Planner 拆成多个 packet；拆不了就退回单 packet。

        ★ 降级不是可选项，是必需：拆解依赖一次模型调用，
          而模型调用会失败（5xx、超时、输出不是 JSON）。
          让整个需求因为「拆解失败」而失败，是把一个**可降级**的故障
          变成了终局 —— 单 packet 虽然产出上限低，但它一直是能用的。

        ★ 降级要出声。静默退回单 packet，使用者会以为多 Agent 生效了，
          而实际只有一个 coder 在干活。
        """

        if not self.config.enable_planner:
            return (self._single_packet(requirement, packet_id, owns, reads, budget_cny),)

        try:
            tasks = self._decompose(requirement)
        except Exception as exc:  # noqa: BLE001 —— 见上方注释
            logger.warning("需求拆解失败，降级为单 packet：%s", exc)
            return (self._single_packet(requirement, packet_id, owns, reads, budget_cny),)

        if len(tasks) < 2:
            # ★ 只拆出一个任务时不值得走多 packet 流程 ——
            #   QA + impl + 集成三个 packet 去做一件事，成本翻三倍而产出不变。
            logger.info("需求只拆出 1 个任务，按单 packet 处理")
            return (self._single_packet(requirement, packet_id, owns, reads, budget_cny),)

        packets = build_packets_from_plan(
            tasks,
            requirement=requirement,
            model=self.config.model,
            effort=self.config.effort,
            total_budget_cny=budget_cny * len(tasks),
            # ★ 必须按角色取模型：qa / reviewer 声明了 mustDifferFrom: [coder]，
            #   共用一个模型会被准入以 MODEL_ISOLATION 拒绝。
            #   这条不变量的意义正是「同一模型既写又审等于没审」。
            model_by_role=self._model_by_role(),
        )
        logger.info(
            "需求拆成 %d 个任务 / %d 个 packet：%s",
            len(tasks),
            len(packets),
            "、".join(t.module for t in tasks),
        )
        return packets

    def _decompose(self, requirement: str) -> tuple[PlannedTask, ...]:
        """调一次模型把需求拆成任务列表。"""

        import asyncio

        gateway = build_model_gateway(self._gateway_config())

        async def run() -> str:
            session = await gateway.open(
                "planner",
                ModelRouting(model=self.config.model, effort=self.config.effort),  # type: ignore[arg-type]
                self.config.packet_budget_cny,
            )
            try:
                response = await session.invoke(
                    ModelRequest(
                        system="你是研发规划者。只输出 JSON，不要任何解释性文字。",
                        messages=(ModelMessage(role="user", content=plan_prompt(requirement)),),
                    )
                )
                return response.text or ""
            finally:
                await session.close()

        return parse_plan(asyncio.run(asyncio.wait_for(run(), timeout=120.0)))

    def _model_by_role(self) -> dict[str, str]:
        """从 RoleSpec 读各角色的默认模型。

        ★ 真源是 B 的 `modelPolicy.defaultModel`，不是引擎的配置 ——
          引擎的 `config.model` 只是**降级用的兜底**。
        """

        table: dict[str, str] = {}
        for spec in self._role_specs:
            policy = getattr(spec, "modelPolicy", None)
            default = getattr(policy, "defaultModel", None) if policy else None
            if isinstance(default, str) and default:
                table[str(spec.id)] = default
        return table

    def _single_packet(
        self,
        requirement: str,
        packet_id: PacketId,
        owns: tuple[str, ...],
        reads: tuple[str, ...],
        budget_cny: float,
    ) -> WorkPacket:
        return build_packet_for_requirement(
            packet_id=packet_id,
            requirement=requirement,
            owns_paths=owns,
            reads_paths=reads,
            model=self.config.model,
            effort=self.config.effort,
            budget_cny=budget_cny,
            acceptance_author=choose_acceptance_author(
                [str(spec.id) for spec in self._role_specs], packet_role="coder"
            ),
        )

    def _gateway_config(self) -> ModelGatewayConfig:
        return ModelGatewayConfig.bailian(
            pricing={
                model: TokenPricingConfig.from_pricing(price)
                for model, price in audited_bailian_pricing().items()
            },
            api_key_env=self._key_env or "",
        )

    def _workers_root(self) -> Path:
        """worker 工作区的父目录 —— 必须与 `_build_spawn_request` 算出来的一致。

        ★ 控制平面把 workspace 定在 `state_dir.parent.parent / codentum-workers`。
          这里重算一遍是重复，但**重复优于猜错**：算错的后果是验收永远
          找不到工作区、于是永远不通过，而那看起来像「模型没干活」。
        """

        return self.config.resolved_state_dir().parent.parent / "codentum-workers"

    def _transition_table(self) -> TransitionTable | None:
        if not self.config.enforce_role_transitions:
            return None
        return TransitionTable(self._role_specs)

    def _build_worker_runtime(self) -> LocalWorkerRuntime | None:
        if self._key_env is None:
            return None

        gateway_config = ModelGatewayConfig.bailian(
            pricing={
                model: TokenPricingConfig.from_pricing(price)
                for model, price in audited_bailian_pricing().items()
            },
            api_key_env=self._key_env,
        )

        if self.config.enable_tool_loop:
            # ★ 带工具循环的 runner：模型能真的把代码写进文件。
            #   不走 build_local_worker_runtime 是因为 RunnerConfig 只认
            #   harness 内置的两种 runner，而这个 runner 属于装配层。
            return LocalWorkerRuntime(
                repo_root=self.config.project_root,
                runner=build_agent_runner(
                    AgentRunnerConfig(
                        gateway=build_model_gateway(gateway_config),
                        timeout_seconds=self.config.model_timeout_seconds,
                        max_turns=self.config.max_tool_turns,
                        # ★ 由 service 传权威路径，而不是让 runner 按 workspace 猜 ——
                        #   `resolved_state_dir()` 可被显式覆盖，猜错会分叉成两个
                        #   记忆库，两边各自看起来都正常，只是谁也攒不够晋级次数。
                        memory_dir=self.config.resolved_state_dir() / "memory",
                        # ★ 主 Agent 连一次，所有 packet 共享同一个工具箱。
                        mcp_toolbox=self._ensure_mcp(),
                    )
                ),
                role_specs=self._role_specs,
                context_loader=self._context_loader,
                context_char_budget=self.config.context_char_budget,
            )

        return build_local_worker_runtime(
            LocalWorkerRuntimeConfig(
                repo_root=self.config.project_root,
                runner=RunnerConfig.model_gateway(
                    gateway_config, timeout_seconds=self.config.model_timeout_seconds
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

        requirement_record = self._requirements.record_for(str(request.packet_id))
        text = requirement_record.get("text") if requirement_record is not None else None
        if not text:
            return ()
        candidates: list[ContextCandidate] = [
            ContextCandidate(
                ref=f"requirement:{request.packet_id}",
                artifact_path=f"requirements/{request.packet_id}.json",
                text=text,
                required=True,
                summary="操作者提交的需求原文",
                priority=0,
            ),
        ]
        payload = requirement_record.get("payload") if requirement_record is not None else {}
        submitted_at = requirement_record.get("submittedAt") if requirement_record is not None else None
        try:
            sources = knowledge_sources_from_payload(
                payload if isinstance(payload, dict) else {},
                packet_id=request.packet_id,
                role=role_spec.id,
            )
            index = PersistentMemoryIndex(self.config.resolved_state_dir() / "memory" / "index")
            if sources:
                index_knowledge_sources_now(
                    index,
                    sources,
                    created_at=submitted_at if isinstance(submitted_at, str) else _now_iso(),
                )
            # ★ 检索必须在 `if sources` **外面**。
            #
            #   原先它嵌在里面，后果是：没有用户提供知识资源的那些执行，
            #   记忆一次都不会被读。而进化层沉淀下来的经验恰恰是**系统自己攒的**，
            #   它的存在不该以「用户这次顺便传了几篇文档」为条件。
            #
            #   这个缺陷是静默的：写入侧一直在攒 L0/L1，读取侧一次没读过，
            #   从外面看就是「记忆系统在跑，只是好像没起作用」。
            candidates.extend(
                memory_context_candidates_now(
                    index,
                    query_text=text,
                    role_spec=role_spec,
                    packet_id=request.packet_id,
                    limit=5,
                    char_budget=max(1, self.config.context_char_budget // 2),
                )
            )
            # ★ 经验要单独召回一次，不能和上面那次共用。
            #   上面用**需求文本**做词法检索，对领域知识是对的；
            #   而经验的相关性来自「你是这个角色」，与本次需求内容无关 ——
            #   共用一次的话，词法得分为 0 会把经验全部丢掉，
            #   于是写入侧一直在攒、读取侧一条都出不来。
            candidates.extend(
                experience_context_candidates_now(
                    index,
                    role_spec=role_spec,
                    packet_id=request.packet_id,
                    char_budget=max(1, self.config.context_char_budget // 4),
                )
            )
        except ResourceSelectionError as exc:
            logger.warning("MemoryIndex 资源选择被拒绝：%s", exc)
        return tuple(candidates)

    # ══════════════════════════════════════════════════════════

    def _record_judgement(
        self, packet_id: str, rule: str, mode: str, fired: bool, code: str | None
    ) -> None:
        """把一次判据评估追加到命中账本。

        ★ 为什么连**没命中**的也记：资产负债表要靠它区分两件完全不同的事 ——
          「这条判据跑过 N 次、一次没命中」与「根本没人在记录」。
          前者说明它可能是多余的，后者说明观测本身坏了。
          只记命中的话，两者在账本上都是「没有这条判据的行」。

        ★ 追加而非覆盖：判据的命中是**跨天累积**的证据，
          晋级条件（在真实案例上命中过 ≥1 次）要靠这段历史来判。
        """

        try:
            ledger_dir = self.config.resolved_state_dir() / "judgements"
            ledger_dir.mkdir(parents=True, exist_ok=True)
            row = {
                "at": _now_iso(),
                "packet": packet_id,
                "rule": rule,
                "mode": mode,
                "fired": fired,
                "code": code,
            }
            with (ledger_dir / "hits.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError as exc:
            # ★ 记账失败不能拖垮准入 —— 但也不能静默：
            #   账本悄悄停止增长，会让资产负债表上的「命中 0 次」变成假数。
            logger.warning("判据命中记录写入失败：%s", exc)

    def _ensure_mcp(self) -> McpToolbox | None:
        """连一次 MCP，之后所有 packet 共享。

        ★ 「连一次」是这里的全部意义。原先 `_AgentRun.__init__` 每个 packet
          连一遍 —— 8 路并行就是 48 个 npx 进程，且与设计里那句
          「主 Agent 接一次」直接冲突。现在 runner 只收已连好的工具箱，
          **它已经不知道该怎么连了**，这个错误在结构上不可能再犯。

        ★ 懒连接而非启动即连：没有 packet 要跑时不该有 npx 进程在后台待着，
          handshake 也不该被几秒的 server 启动拖住。

        ★ 连接结果落盘。没有它的话，「没配置」「配了但一个都没连上」
          「连上了但模型没调用」这三种情况从外面看是同一个样子 ——
          都是「MCP 好像没起作用」，而它们的处理办法完全不同。
        """

        if self.config.mcp_config_dir is None:
            return None
        if self._mcp is not None:
            return self._mcp

        self._mcp = build_mcp_toolbox(self.config.mcp_config_dir)
        report_dir = self.config.resolved_state_dir() / "mcp"
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / "connections.json").write_text(
            json.dumps(
                {
                    "configDir": str(self.config.mcp_config_dir),
                    "connectedAt": _now_iso(),
                    "toolCount": len(self._mcp.schemas()),
                    # ★ 没被加载的条目也要列出来并说明原因。
                    #   否则「目录写错了」「配置全是关的」「连上了但模型没调用」
                    #   三种情况在报告里长得一模一样 —— 而它们的解法完全不同。
                    "skipped": list(describe_skipped(self.config.mcp_config_dir)),
                    "servers": [
                        {
                            "id": r.server_id,
                            "name": r.name,
                            "connected": r.connected,
                            "toolCount": r.tool_count,
                            # ★ 失败原因原样写出来 —— 缺哪个环境变量必须说出名字，
                            #   否则使用者只看到「没连上」，不知道该去配什么。
                            "error": r.error,
                        }
                        for r in self._mcp.reports
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        logger.info("MCP 已连接：%s", self._mcp.summary())
        return self._mcp

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
