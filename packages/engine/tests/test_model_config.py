"""三级模型配置的判据。

★ 这组守的是一句话：**界面上配的东西，必须真的生效。**

  在它之前，桌面端能存 API Key、界面显示「已配置」，而引擎读的是操作系统
  环境变量 —— 两边从未连通。那种缺陷的特征是**它有一个会撒谎的成功状态**，
  比功能缺失难查得多。
"""

from __future__ import annotations

import json
from pathlib import Path

from codentum_contracts.state import ModelPolicy, RoleSpec
from codentum_roles.loader import load_builtin_role_specs

from codentum_engine.model_config import (
    CONFIG_FILENAME,
    ORCHESTRATOR_ROLE,
    SCHEMA,
    ModelConfig,
    ModelOverride,
    Resolution,
    agent_key_env,
    load_model_config,
    with_agent_key_env,
)

REPO_ENGINE = Path(__file__).resolve().parents[1]

DEFAULT_MODEL = "qwen-coder-plus-1106"
DEFAULT_EFFORT = "medium"


def resolve(config: ModelConfig, role: str, specs: tuple[RoleSpec, ...] = ()) -> Resolution:
    """★ 显式传参而不是 `**dict` —— mypy 抓到过：`**dict[str, str]` 会被
    当成可能填到任何一个形参上，包括 role_specs。类型检查看不穿的调用，
    人也看不穿。"""

    return config.resolve(
        role, role_specs=specs, fallback_model=DEFAULT_MODEL, fallback_effort=DEFAULT_EFFORT
    )


def _write(state: Path, payload: dict[str, object]) -> Path:
    state.mkdir(parents=True, exist_ok=True)
    (state / CONFIG_FILENAME).write_text(
        json.dumps({"schema": SCHEMA, **payload}, ensure_ascii=False), encoding="utf-8"
    )
    return state


def _spec(role: str, model: str | None = None, effort: str | None = None) -> RoleSpec:
    """★ 以**真实的内置 RoleSpec** 为底，只改 modelPolicy。

    手搓一份最小 RoleSpec 会与契约漂移（这次就撞上了：writes / reads /
    tools / transitions 都是必填）。以真的为底再 `model_copy`，
    契约加字段时这里不会假装还能用。
    """

    base = next(s for s in load_builtin_role_specs() if s.id == role)
    return base.model_copy(
        update={"modelPolicy": ModelPolicy(defaultModel=model, defaultEffort=effort)}  # type: ignore[arg-type]
    )


# ══════════════════════════════════════════════════════════════
#  三级优先级
# ══════════════════════════════════════════════════════════════


def test_nothing_configured_falls_back_to_the_command_line_default(tmp_path: Path) -> None:
    """★ 没配过的项目就该这样 —— 文件不存在是**正常状态**，不是错误。"""

    resolved = resolve(load_model_config(tmp_path), "coder")
    assert resolved.model == "qwen-coder-plus-1106"
    assert resolved.source["model"] == "fallback"


def test_global_applies_to_every_agent(tmp_path: Path) -> None:
    _write(tmp_path, {"global": {"model": "qwen3-max", "effort": "high"}})
    config = load_model_config(tmp_path)

    for role in ("coder", "qa", "reviewer", ORCHESTRATOR_ROLE):
        resolved = resolve(config, role)
        assert resolved.model == "qwen3-max", role
        assert resolved.source["model"] == "global"


def test_orchestrator_override_does_not_leak_to_sub_agents(tmp_path: Path) -> None:
    """★ 「只对主 Agent」必须真的只对主 Agent。

    若主 Agent 那一层被当成「所有角色的第二默认」，给主 Agent 换个强模型
    会把所有子 Agent 一起换掉 —— 而使用者的本意恰恰相反。
    这条测的就是那个泄漏。
    """

    _write(
        tmp_path,
        {
            "global": {"model": "qwen-plus"},
            "orchestrator": {"model": "qwen3-max", "effort": "xhigh"},
        },
    )
    config = load_model_config(tmp_path)

    main = resolve(config, ORCHESTRATOR_ROLE)
    assert main.model == "qwen3-max"
    assert main.effort == "xhigh"
    assert main.source["model"] == "orchestrator"

    sub = resolve(config, "coder")
    assert sub.model == "qwen-plus", "主 Agent 的配置泄漏到了子 Agent"
    assert sub.source["model"] == "global"


def test_per_agent_override_wins_over_everything(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "global": {"model": "qwen-plus", "effort": "low"},
            "agents": {"coder": {"model": "deepseek-v4-pro", "effort": "max"}},
        },
    )
    config = load_model_config(tmp_path)

    coder = resolve(config, "coder", (_spec("coder", "qwen-coder-plus"),))
    assert coder.model == "deepseek-v4-pro"
    assert coder.effort == "max"
    assert coder.source == {"model": "agent", "effort": "agent"}

    qa = resolve(config, "qa")
    assert qa.model == "qwen-plus", "给 coder 的配置不该影响 qa"


def test_fields_fall_through_independently(tmp_path: Path) -> None:
    """★ 字段级穿透，不是整块覆盖。

    只想给 coder 提高 effort 的人，不该被迫把 model 也抄一遍 ——
    抄一遍就意味着全局改了之后 coder 不会跟着改，
    而**那种漂移不会有任何东西报错**。
    """

    _write(
        tmp_path,
        {
            "global": {"model": "qwen3-max", "effort": "low", "baseUrl": "https://gw.example/v1"},
            "agents": {"coder": {"effort": "max"}},
        },
    )
    resolved = resolve(load_model_config(tmp_path), "coder")

    assert resolved.effort == "max" and resolved.source["effort"] == "agent"
    assert resolved.model == "qwen3-max" and resolved.source["model"] == "global"
    assert resolved.base_url == "https://gw.example/v1"


def test_global_outranks_the_role_spec_default(tmp_path: Path) -> None:
    """★ 这条曾经是反的，**端到端一跑就发现那是错的**。

    第一版把 RoleSpec 排在全局之上，理由是「modelPolicy 是角色固有的能力要求」。
    实测：11 个角色里 10 个写了 defaultModel，唯一没写的 guardian 恰好是
    唯一 usesModel=false 的角色 —— 于是「全局模型」对**所有会调模型的角色
    都不生效**，是一个点了没有任何效果的设置。

    ★ 错在把两样东西混为一谈：`defaultModel` 名字就叫默认值，
      真正的约束是 `mustDifferFrom`。默认值就该被显式配置覆盖。
    """

    _write(tmp_path, {"global": {"model": "qwen-plus"}})
    specs = (_spec("coder", "deepseek-v4-pro"),)
    resolved = resolve(load_model_config(tmp_path), "coder", specs)
    assert resolved.model == "qwen-plus", "全局模型没有生效 —— 它又变成死设置了"
    assert resolved.source["model"] == "global"

    # 该 Agent 的显式配置仍然最高
    _write(tmp_path, {"global": {"model": "qwen-plus"}, "agents": {"coder": {"model": "my-model"}}})
    assert resolve(load_model_config(tmp_path), "coder", specs).model == "my-model"


def test_global_does_not_break_model_isolation(tmp_path: Path) -> None:
    """★ 全局覆盖 RoleSpec，**但不能压过 mustDifferFrom**。

    `mustDifferFrom` 说的是「同一模型既写又审 → 盲区重叠 → 评审失效」。
    若全局值让 qa 和 coder 落到同一个模型，这条不变量就破了 ——
    而破的方式是**静默**的：评审照常进行，只是评审不再独立。

    所以撞上隔离时该角色保留自己的默认值，并把来源标成 roleSpecIsolation，
    让界面能解释「为什么这个角色没跟随全局」。
    """

    _write(tmp_path, {"global": {"model": "same-model"}})
    specs = tuple(load_builtin_role_specs())  # qa 真的声明了 mustDifferFrom=[coder]
    coder = resolve(load_model_config(tmp_path), "coder", specs)
    qa = resolve(load_model_config(tmp_path), "qa", specs)

    assert coder.model == "same-model", "coder 应当跟随全局"
    assert qa.model != coder.model, "隔离被全局值破掉了 —— 同一模型既写又审"
    assert qa.source["model"] == "roleSpecIsolation", "没有说明为什么它不跟随全局"


def test_explicit_per_agent_config_still_wins_over_isolation(tmp_path: Path) -> None:
    """★ 使用者**针对某个角色显式配置**的模型仍然最高，即使撞上隔离。

    那是明示意图，而准入的 check_role_model_isolation 会当场拒绝并说明。
    **「拦在准入」比「悄悄替他改掉」好** —— 后者会让使用者以为自己配上了。
    """

    _write(tmp_path, {"agents": {"qa": {"model": "qwen-coder-plus-1106"}}})
    specs = tuple(load_builtin_role_specs())
    qa = resolve(load_model_config(tmp_path), "qa", specs)
    assert qa.model == "qwen-coder-plus-1106"
    assert qa.source["model"] == "agent", "显式配置被隔离逻辑悄悄改掉了"


# ══════════════════════════════════════════════════════════════
#  ★ source —— 这次改动的另一半价值
# ══════════════════════════════════════════════════════════════


def test_resolution_reports_which_layer_each_value_came_from(tmp_path: Path) -> None:
    """★ 「配了没生效」的三种原因现象完全一样，修法完全不同。

    没保存？被 RoleSpec 覆盖了？引擎没读到文件？
    没有 source 的话使用者只能猜。
    """

    _write(
        tmp_path,
        {
            "global": {"model": "qwen-plus"},
            "agents": {"coder": {"effort": "high"}},
        },
    )
    resolved = resolve(load_model_config(tmp_path), "coder", (_spec("coder"),))

    assert resolved.source["model"] == "global"
    assert resolved.source["effort"] == "agent"
    assert resolved.as_json()["source"]["model"] == "global"


# ══════════════════════════════════════════════════════════════
#  坏输入：降级但不静默、不崩
# ══════════════════════════════════════════════════════════════


def test_broken_json_does_not_stop_the_engine(tmp_path: Path) -> None:
    """★ 一个手工编辑坏了的 JSON 不该让整个引擎起不来。"""

    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / CONFIG_FILENAME).write_text("{ 这不是 JSON", encoding="utf-8")
    resolved = resolve(load_model_config(tmp_path), "coder")
    assert resolved.model == "qwen-coder-plus-1106"
    assert resolved.source["model"] == "fallback", "坏文件必须表现为「没有任何一层生效」"


def test_wrong_schema_is_ignored(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / CONFIG_FILENAME).write_text(
        json.dumps({"schema": "codentum.model-config.v2", "global": {"model": "x"}}),
        encoding="utf-8",
    )
    assert resolve(load_model_config(tmp_path), "coder").model != "x"


def test_illegal_effort_degrades_instead_of_blocking_startup(tmp_path: Path) -> None:
    """★ 一个字段的笔误不该挡住整个引擎启动，但也不能装作配过。"""

    _write(tmp_path, {"agents": {"coder": {"model": "ok-model", "effort": "超高"}}})
    resolved = resolve(load_model_config(tmp_path), "coder")
    assert resolved.model == "ok-model"
    assert resolved.effort == "medium"
    assert resolved.source["effort"] == "fallback", "非法 effort 必须表现为「没配」"


def test_empty_strings_count_as_not_configured(tmp_path: Path) -> None:
    """★ 界面上把输入框清空 = 取消配置，不是「配了一个空模型名」。

    空字符串若被当成有效值，会一路传到 provider 那边报「模型不存在」，
    而真因是这里没把空当成缺省。
    """

    _write(tmp_path, {"global": {"model": "qwen3-max"}, "agents": {"coder": {"model": "   "}}})
    resolved = resolve(load_model_config(tmp_path), "coder")
    assert resolved.model == "qwen3-max"


# ══════════════════════════════════════════════════════════════
#  密钥：只存变量名，不存密钥
# ══════════════════════════════════════════════════════════════


def test_config_file_carries_env_names_never_secrets(tmp_path: Path) -> None:
    """★ 密钥经 safeStorage 加密保管，在拉起引擎那一刻作为环境变量注入。

    把密钥写进这个 JSON 会让那层加密变成装饰。
    这条守的是**契约里根本没有存密钥的地方**。
    """

    from dataclasses import fields

    names = {f.name for f in fields(ModelOverride)}
    assert "api_key" not in names and "apiKey" not in names
    assert "api_key_env" in names


def test_agent_key_env_is_defined_in_exactly_one_place() -> None:
    """★ 桌面端按同一约定注入。两边各写一份字符串拼接是危险的。"""

    assert agent_key_env("coder") == "CODENTUM_AGENT_KEY__CODER"
    assert agent_key_env("planner") == "CODENTUM_AGENT_KEY__PLANNER"


def test_with_agent_key_env_only_fills_the_gap(tmp_path: Path) -> None:
    """显式配了 apiKeyEnv 的角色不该被兜底覆盖。"""

    config = ModelConfig(agents={"coder": ModelOverride(api_key_env="MY_OWN_KEY")})
    filled = with_agent_key_env(config, ("coder", "qa"))

    assert filled.agents["coder"].api_key_env == "MY_OWN_KEY"
    assert filled.agents["qa"].api_key_env == agent_key_env("qa")


# ══════════════════════════════════════════════════════════════
#  ★ 接线 —— 这次最容易再次断掉的地方
# ══════════════════════════════════════════════════════════════
#
# 上面那些测的是「解析算得对不对」。但这次改动的**全部起因**是：
# 解析对了、界面存了，而引擎那三处调用点各自用着 `self.config.model`。
# 所以必须有测试盯住**引擎真的按解析结果去跑**。


def test_engine_resolves_through_one_entry_point() -> None:
    """★ 三处调用点必须收敛到 `resolve_model_for`。

    此前规划、多 packet、单 packet 三处各算各的 —— 于是
    「界面上配了 coder 的模型」在其中两处生效、一处不生效是完全可能的，
    而且不会有任何东西报错。这条用源码结构守住那个收敛。
    """

    import ast

    source = (
        REPO_ENGINE / "codentum_engine" / "service.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    # 找出 EngineService 里所有构造 ModelRouting / 传 model= 的地方
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name not in {"build_packet_for_requirement", "build_packets_from_plan"}:
            continue
        for kw in node.keywords:
            if kw.arg != "model":
                continue
            # 允许 resolve 出来的值；不允许直接 self.config.model
            rendered = ast.unparse(kw.value)
            if "self.config.model" in rendered:
                offenders.append(f"{name}(model={rendered})")

    assert not offenders, (
        "这些调用点仍在用全局默认，绕过了三级解析：" + "；".join(offenders)
    )


def test_orchestrator_call_site_does_not_hardcode_the_global_model() -> None:
    """★ 主 Agent 的调用点曾经写死 `self.config.model`。

    也就是「只给主 Agent 换个强模型」在**拆解需求这一步完全不生效**，
    而拆解恰恰是最吃模型能力的一步。
    """

    source = (REPO_ENGINE / "codentum_engine" / "service.py").read_text(encoding="utf-8")
    decompose = source[source.index("def _decompose") : source.index("def _model_by_role")]
    assert "resolve_model_for(ORCHESTRATOR_ROLE)" in decompose, "主 Agent 没走三级解析"
    assert "ModelRouting(model=self.config.model" not in decompose, "主 Agent 仍写死全局模型"


def test_env_name_convention_matches_the_desktop_side() -> None:
    """★ 跨语言约定：桌面端 `agentKeyEnvName` 必须与这里生成同样的名字。

    两边各断言一个字面量是不够的 —— 那只证明「各自没改」，
    证明不了「两边一致」。改了一边、两边的测试都还是绿的，
    而现象是「配了 Key 但那个 Agent 用不上」。
    这条直接去读桌面端的源码，比对它的实现。
    """

    import re

    ts_path = (
        REPO_ENGINE.parent / "desktop" / "shell" / "main" / "python-engine" / "SidecarManager.ts"
    )
    assert ts_path.is_file(), f"找不到桌面端源码：{ts_path}（这条测试会变成空转）"
    ts = ts_path.read_text(encoding="utf-8")

    match = re.search(r"export function agentKeyEnvName\(role: string\): string \{\s*return `([^`]+)`", ts)
    assert match is not None, "桌面端没有 agentKeyEnvName —— 约定的一端不见了"

    template = match.group(1)
    # `CODENTUM_AGENT_KEY__${role.toUpperCase()}` → 用 Python 侧的规则渲染同一个角色
    rendered = template.replace("${role.toUpperCase()}", "CODER")
    assert rendered == agent_key_env("coder"), (
        f"两边的环境变量名约定不一致：桌面端 {rendered!r} vs 引擎 {agent_key_env('coder')!r}"
    )


# ══════════════════════════════════════════════════════════════
#  系统提示词 —— 叠加，不是覆盖
# ══════════════════════════════════════════════════════════════


def test_prompts_stack_instead_of_overriding(tmp_path: Path) -> None:
    """★ 与模型的合并规则**刻意不同**。

    在全局写了团队规范、又给 coder 补一句要求的人，期望两条都生效。
    若按覆盖处理，给某个 Agent 写一句话会让全局那条**静默消失** ——
    而现象是「模型不守规范了」，没人会想到去查提示词的合并规则。
    """

    _write(
        tmp_path,
        {
            "global": {"systemPrompt": "所有代码必须写中文注释。"},
            "agents": {"coder": {"systemPrompt": "优先用组合而不是继承。"}},
        },
    )
    notes = load_model_config(tmp_path).resolve_prompt("coder")

    assert [text for _, text in notes] == ["所有代码必须写中文注释。", "优先用组合而不是继承。"]
    assert [scope for scope, _ in notes] == ["全局", "Agent coder"], "来源标注丢了就无法归因"


def test_prompt_order_is_broad_to_specific(tmp_path: Path) -> None:
    """★ 顺序是 全局 → 主 Agent → 该 Agent。

    模型对靠后的内容更敏感，而越具体的要求越该压过泛泛的规范。
    """

    _write(
        tmp_path,
        {
            "global": {"systemPrompt": "G"},
            "orchestrator": {"systemPrompt": "O"},
            "agents": {"planner": {"systemPrompt": "A"}},
        },
    )
    assert [t for _, t in load_model_config(tmp_path).resolve_prompt("planner")] == ["G", "O", "A"]


def test_orchestrator_prompt_does_not_leak_to_sub_agents(tmp_path: Path) -> None:
    """给主 Agent 写的话不该出现在 coder 的提示词里。"""

    _write(tmp_path, {"orchestrator": {"systemPrompt": "只输出 JSON。"}})
    assert load_model_config(tmp_path).resolve_prompt("coder") == ()
    assert len(load_model_config(tmp_path).resolve_prompt(ORCHESTRATOR_ROLE)) == 1


def test_blank_prompt_is_not_a_note(tmp_path: Path) -> None:
    """★ 界面上把提示词清空 = 不追加，而不是追加一段空白。

    追加空白会在提示词里留下一个没有内容的 `### 来自：…` 小节 ——
    模型会看到一个空指令，而使用者以为自己已经删掉了。
    """

    _write(tmp_path, {"agents": {"coder": {"systemPrompt": "   \n  "}}})
    assert load_model_config(tmp_path).resolve_prompt("coder") == ()


def test_engine_wires_the_prompt_resolver_into_the_runtime() -> None:
    """★ 守接线本身。

    此前 systemPrompt 存了、加密了、界面显示已保存，而引擎从不读它 ——
    这一条盯的正是那段链路不能再断。
    """

    source = (REPO_ENGINE / "codentum_engine" / "service.py").read_text(encoding="utf-8")
    assert "operator_note_resolver=self.resolve_prompt_for" in source, (
        "生产装配没有把提示词解析器传给 runtime —— 界面填的提示词到不了模型"
    )
