"""模型接入配置 —— 全局 / 主 Agent / 各子 Agent 三级覆盖。

════════════════════════════════════════════════════════════════
 ★ 这个模块补的是哪个洞
════════════════════════════════════════════════════════════════

在它之前，桌面端**有配置界面**（`RolesView` 能填 API Key 与提示词，
凭据经 Electron `safeStorage` 加密落盘），而引擎侧：

  · 模型来自命令行 `--model`，全局一个值
  · API Key 来自**操作系统环境变量**（`DASHSCOPE_API_KEY` 等）
  · 从不读桌面端写的 `configuration.json`

也就是说：**使用者在界面上配了 Key，界面显示「已配置」，
引擎照旧读环境变量。** 界面说配好了，系统说没有 Key。

★ 这比「缺功能」严重：缺功能是看得见的，而一个**会撒谎的已配置状态**
  会把排查引到完全错误的方向。所以这次除了接线，还要让
  「实际生效的是什么、来自哪一层」成为可观测的产出（见 `Resolution.source`）。

════════════════════════════════════════════════════════════════
 ★ 三级覆盖，以及为什么每一级都要能单独关掉
════════════════════════════════════════════════════════════════

    全局默认  →  主 Agent（planner）  →  各子 Agent（11 个角色）
    ← 优先级低                              优先级高 →

每一层的每个字段都可以缺省，缺省就穿透到下一层。所以：

  · 只配全局           → 所有 Agent 用同一套
  · 只配主 Agent       → 规划用强模型，干活的用默认
  · 只配某个子 Agent   → 单独给 coder 换模型，其余不动

★ 字段级穿透（而不是整块覆盖）是刻意的：只想给 coder 提高 effort 的人，
  不应该被迫把 model / baseUrl / key 全抄一遍 —— 抄一遍就意味着
  全局改了之后 coder 不会跟着改，而那种漂移**不会有任何东西报错**。

════════════════════════════════════════════════════════════════
 ★ 密钥不在这个文件里
════════════════════════════════════════════════════════════════

这里只存**环境变量名**（`apiKeyEnv`），不存密钥本身。

桌面端用 `safeStorage` 加密保管密钥，在**拉起引擎进程的那一刻**解密并
作为环境变量注入。密钥因此从不以明文落盘 —— 而这正是 safeStorage 的
全部意义所在。把密钥写进这个 JSON 会让那层加密变成装饰。

★ 对应的另一半在桌面端 `SidecarManager`；两边的命名约定见 `agent_key_env`。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from codentum_contracts.state import ModelRouting, RoleSpec

__all__ = [
    "CONFIG_FILENAME",
    "ORCHESTRATOR_ROLE",
    "SCHEMA",
    "ModelConfig",
    "ModelOverride",
    "Resolution",
    "agent_key_env",
    "load_model_config",
]

SCHEMA = "codentum.agent-config.v1"
CONFIG_FILENAME = "agent-config.json"
"""★ 从 `model-config.json` 改名而来（同一天内，无外部消费者）。

改名的理由是文件名得说真话：它现在除了模型接入，还装各层的系统提示词。
一个叫 `model-config` 的文件里放提示词，下一个人找提示词时不会想到打开它 ——
**名字不准确的代价，是它会持续误导每一个后来的人。**
"""

ORCHESTRATOR_ROLE = "planner"
"""「主 Agent」在代码里的角色 id。

★ 主 Agent 不是第 12 个角色，而是 11 个角色里**承担编排职责**的那个：
  它把需求拆成 packet，决定谁干什么。界面上叫「主 Agent」是因为
  对使用者而言它就是那个统筹的；代码里它是 planner。
  这里显式写一个常量而不是各处写字面量 —— 哪天编排换了角色，
  只改这里，而不是去找所有写着 "planner" 的地方。
"""

_ALLOWED_EFFORT = frozenset({"low", "medium", "high", "xhigh", "max"})


@dataclass(frozen=True, slots=True)
class ModelOverride:
    """一层配置。每个字段都可缺省，缺省即穿透到下一层。"""

    model: str | None = None
    effort: str | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    system_prompt: str | None = None
    """这一层追加的系统提示词。

    ★ 合并规则与上面几个字段**不同**：模型是「覆盖」（高层赢），
      提示词是「叠加」（各层都生效，按 全局 → 主Agent → 该Agent 依次拼接）。

      理由是使用者的直觉：在全局写了一条团队规范，又给 coder 补一句
      具体要求，他期望**两条都生效** —— 而不是后者让前者消失。
      两种合并规则放在同一个类里是刻意的取舍：字段少、语义各自写清楚，
      好过拆成两套结构再想办法让它们保持一致。
    """

    @property
    def is_empty(self) -> bool:
        return not any(
            (self.model, self.effort, self.base_url, self.api_key_env, self.system_prompt)
        )

    def over(self, base: ModelOverride) -> ModelOverride:
        """把自己叠在 `base` 上：自己有值的字段生效，没值的沿用 base。"""

        return ModelOverride(
            model=self.model or base.model,
            effort=self.effort or base.effort,
            base_url=self.base_url or base.base_url,
            api_key_env=self.api_key_env or base.api_key_env,
            system_prompt=self.system_prompt or base.system_prompt,
        )


Layer = Literal[
    "agent",
    "orchestrator",
    "roleSpec",
    "roleSpecIsolation",
    "global",
    "fallback",
]


@dataclass(frozen=True, slots=True)
class Resolution:
    """某个角色最终生效的配置，**以及每个值来自哪一层**。

    ★ `source` 不是锦上添花，它是这次改动的一半价值。

      使用者在界面上给 coder 配了模型，跑起来却是另一个 —— 没有 source
      的话，他只能猜：是没保存？是被 RoleSpec 覆盖了？是引擎没读到文件？
      三种原因的修法完全不同，而现象一模一样。

      有了 source，界面可以直接显示「model 来自 全局 / effort 来自 该 Agent」。
    """

    role: str
    model: str
    effort: str
    base_url: str | None
    api_key_env: str | None
    source: dict[str, Layer]

    def to_routing(self) -> ModelRouting:
        return ModelRouting(model=self.model, effort=self.effort, batch=None)  # type: ignore[arg-type]

    def as_json(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "model": self.model,
            "effort": self.effort,
            "baseUrl": self.base_url,
            "apiKeyEnv": self.api_key_env,
            "source": dict(self.source),
        }


def agent_key_env(role: str) -> str:
    """某个角色专属 API Key 的环境变量名。

    ★ 桌面端按同一个约定注入。两边各写一份字符串拼接是危险的 ——
      所以这个函数是**唯一的定义处**，桌面端那侧的测试会比对它。
    """

    return f"CODENTUM_AGENT_KEY__{role.upper()}"


@dataclass(frozen=True, slots=True)
class ModelConfig:
    """三级配置的全体。"""

    global_: ModelOverride = ModelOverride()
    orchestrator: ModelOverride = ModelOverride()
    agents: dict[str, ModelOverride] = field(default_factory=dict)

    def resolve(
        self,
        role: str,
        *,
        role_specs: tuple[RoleSpec, ...] = (),
        fallback_model: str,
        fallback_effort: str,
    ) -> Resolution:
        """算出某个角色最终用什么。

        优先级（高 → 低）：

            该子 Agent  >  主 Agent（仅当 role 就是主 Agent）
                        >  全局  >  RoleSpec.modelPolicy  >  命令行默认

        ★ 主 Agent 那一层**只对主 Agent 生效**，不是「所有角色的第二默认」。
          否则给主 Agent 换个强模型，会把所有子 Agent 一起换掉 ——
          而使用者的本意恰恰是「只对主 Agent」。

        ════════════════════════════════════════════════════
         ★ 全局为什么必须排在 RoleSpec 之上（这里改过一次）
        ════════════════════════════════════════════════════

        第一版把 RoleSpec 排在全局之上，理由是「modelPolicy 是角色固有的
        能力要求」。**端到端一跑就发现那是错的**：

            11 个角色里 10 个写了 defaultModel，
            唯一没写的 guardian 恰好是唯一 usesModel=false 的角色。

        也就是说「全局模型」对**所有真正会调模型的角色都不生效** ——
        它是一个点了没有任何效果的设置。而这正是使用者点名要的功能。

        ★ 错在把两样东西混为一谈：`defaultModel` 名字就叫**默认值**，
          而真正的约束是 `mustDifferFrom`。默认值就该被显式配置覆盖。

        ════════════════════════════════════════════════════
         ★ 但全局不能压过隔离约束
        ════════════════════════════════════════════════════

        `mustDifferFrom` 声明的是「同一模型既写又审 → 盲区重叠 → 评审失效」。
        若全局值让 qa 和 coder 落到同一个模型，这条不变量就破了 ——
        而破的方式是**静默**的：评审照常进行，只是评审不再独立。

        所以：全局覆盖 RoleSpec，**但当这么做会撞上 mustDifferFrom 时，
        该角色保留自己的 RoleSpec 默认值**，并把来源标成
        `roleSpecIsolation` —— 让界面能解释「为什么这个角色没跟随全局」。

        ★ 使用者**针对某个角色显式配置**的模型仍然最高，即使它撞上隔离 ——
          那是明示意图，而准入的 check_role_model_isolation 会当场拒绝并说明。
          「拦在准入」比「悄悄替他改掉」好：后者会让使用者以为自己配上了。
        """

        source: dict[str, Layer] = {}

        def pick(field: str, *candidates: tuple[str | None, Layer]) -> str | None:
            for value, layer in candidates:
                if value:
                    source[field] = layer
                    return value
            return None

        agent = self.agents.get(role, ModelOverride())
        orch = self.orchestrator if role == ORCHESTRATOR_ROLE else ModelOverride()
        spec = next((s for s in role_specs if s.id == role), None)
        spec_model = spec.modelPolicy.defaultModel if spec and spec.modelPolicy else None
        spec_effort = spec.modelPolicy.defaultEffort if spec and spec.modelPolicy else None

        # ★ 全局排在 RoleSpec 之上（见 docstring），但要先看隔离约束。
        global_model = self.global_.model
        if (
            global_model
            and not agent.model
            and not orch.model
            and spec_model
            and self._breaks_isolation(role, global_model, role_specs)
        ):
            # 跟随全局会撞上 mustDifferFrom —— 保留本角色的 RoleSpec 默认值，
            # 并把这件事标进 source，让界面能解释「为什么它没跟随全局」。
            global_model = None
            source["model"] = "roleSpecIsolation"
            model: str | None = spec_model
        else:
            model = pick(
                "model",
                (agent.model, "agent"),
                (orch.model, "orchestrator"),
                (global_model, "global"),
                (spec_model, "roleSpec"),
                (fallback_model, "fallback"),
            )
        effort = pick(
            "effort",
            (agent.effort, "agent"),
            (orch.effort, "orchestrator"),
            (self.global_.effort, "global"),
            (spec_effort, "roleSpec"),
            (fallback_effort, "fallback"),
        )
        base_url = pick(
            "baseUrl",
            (agent.base_url, "agent"),
            (orch.base_url, "orchestrator"),
            (self.global_.base_url, "global"),
        )
        # ★ Key 的环境变量名有一条额外规则：该角色**自己那个**永远优先，
        #   即使配置文件里没写 —— 因为桌面端注入它时不一定会同时改这个文件。
        #   见 `agent_key_env` 的说明。
        api_key_env = pick(
            "apiKeyEnv",
            (agent.api_key_env, "agent"),
            (orch.api_key_env, "orchestrator"),
            (self.global_.api_key_env, "global"),
        )

        assert model is not None and effort is not None  # fallback 保证非空
        return Resolution(
            role=role,
            model=model,
            effort=effort,
            base_url=base_url,
            api_key_env=api_key_env,
            source=source,
        )



    def _breaks_isolation(
        self, role: str, candidate: str, role_specs: tuple[RoleSpec, ...]
    ) -> bool:
        """跟随 `candidate` 会不会撞上本角色的 mustDifferFrom。

        ★ 比的是对方的 **RoleSpec 默认值 + 全局**两种可能落点，
          而不只是默认值：如果全局也会把对方改成 candidate，那照样撞。

        ★ 不做递归解析：本仓库的 mustDifferFrom 只有 helper/qa/reviewer → coder，
          而 coder 自己没有 mustDifferFrom。真要出现相互声明，
          递归会绕不出来 —— 一层足够，且不会挂死。
        """

        spec = next((s for s in role_specs if s.id == role), None)
        if spec is None or spec.modelPolicy is None:
            return False
        for other_id in spec.modelPolicy.mustDifferFrom or ():
            other = next((s for s in role_specs if s.id == other_id), None)
            if other is None or other.modelPolicy is None:
                continue
            other_agent = self.agents.get(str(other_id), ModelOverride())
            other_model = (
                other_agent.model or self.global_.model or other.modelPolicy.defaultModel
            )
            if other_model and other_model == candidate:
                return True
        return False

    def resolve_prompt(self, role: str) -> tuple[tuple[str, str], ...]:
        """某个角色要追加的系统提示词，按生效顺序返回 `(层名, 文本)`。

        ★ **叠加，不是覆盖** —— 与 `resolve()` 的规则刻意不同。

          在全局写了一条团队规范、又给 coder 补一句具体要求的人，
          期望的是两条都生效。若按覆盖处理，给某个 Agent 写一句话
          会让全局那条**静默消失**，而现象是「模型不守规范了」，
          没有人会想到去查提示词的合并规则。

        ★ 顺序是 全局 → 主 Agent → 该 Agent：从最普适到最具体。
          模型对靠后的内容更敏感，而越具体的要求越该压过泛泛的规范。

        ★ 返回层名而不是拼好的字符串：调用方要把来源标进提示词，
          让读证据的人看得出哪一段是谁加的。一段来源不明的提示词，
          出问题时无法归因。
        """

        ordered: list[tuple[str, str]] = []
        if self.global_.system_prompt:
            ordered.append(("全局", self.global_.system_prompt))
        if role == ORCHESTRATOR_ROLE and self.orchestrator.system_prompt:
            ordered.append(("主 Agent", self.orchestrator.system_prompt))
        agent = self.agents.get(role)
        if agent is not None and agent.system_prompt:
            ordered.append((f"Agent {role}", agent.system_prompt))
        return tuple(ordered)


def _override_from(raw: Any) -> ModelOverride:
    if not isinstance(raw, dict):
        return ModelOverride()
    effort = raw.get("effort")
    if effort is not None and effort not in _ALLOWED_EFFORT:
        # ★ 不静默丢弃也不抛：非法值降级为「没配」，但这件事要能被看见 ——
        #   调用方拿 Resolution.source 一看就知道 effort 没来自 agent 层。
        #   抛异常会让一个字段的笔误挡住整个引擎启动。
        effort = None
    return ModelOverride(
        model=_clean(raw.get("model")),
        effort=_clean(effort),
        base_url=_clean(raw.get("baseUrl")),
        api_key_env=_clean(raw.get("apiKeyEnv")),
        system_prompt=_clean(raw.get("systemPrompt")),
    )


def _clean(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def load_model_config(state_dir: Path | str) -> ModelConfig:
    """从 `<state_dir>/agent-config.json` 读三级配置。

    ★ 文件不存在 = 没有任何覆盖，一切走默认。这是**正常状态**，不是错误：
      没配过的项目就该这样。

    ★ 文件损坏也返回空配置而不是抛：一个手工编辑坏了的 JSON
      不该让整个引擎起不来。但它与「文件不存在」在结果上不可区分，
      所以损坏时**必须留下痕迹** —— 调用方通过 `Resolution.source`
      全是 fallback/roleSpec 能看出没有任何一层生效。
      （更强的做法是把解析错误报进 handshake，留作后续。）
    """

    path = Path(state_dir) / CONFIG_FILENAME
    if not path.is_file():
        return ModelConfig()

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return ModelConfig()

    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
        return ModelConfig()

    agents_raw = raw.get("agents")
    agents: dict[str, ModelOverride] = {}
    if isinstance(agents_raw, dict):
        for role, item in agents_raw.items():
            override = _override_from(item)
            if not override.is_empty:
                agents[str(role)] = override

    return ModelConfig(
        global_=_override_from(raw.get("global")),
        orchestrator=_override_from(raw.get("orchestrator")),
        agents=agents,
    )


def with_agent_key_env(config: ModelConfig, roles: tuple[str, ...]) -> ModelConfig:
    """给每个角色补上「它自己那个专属 Key 环境变量」作为兜底。

    ★ 为什么不在 `resolve` 里直接写死：那样任何角色都会声称自己有
      `CODENTUM_AGENT_KEY__X`，即使那个变量根本不存在 —— 而下游会据此
      去建一个拿不到 Key 的网关。补齐要由**知道哪些角色真的配了 Key 的人**
      来做，也就是装配层。
    """

    agents = dict(config.agents)
    for role in roles:
        current = agents.get(role, ModelOverride())
        if current.api_key_env is None:
            agents[role] = replace(current, api_key_env=agent_key_env(role))
    return ModelConfig(global_=config.global_, orchestrator=config.orchestrator, agents=agents)
