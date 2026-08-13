"""Planner —— 一个需求拆成多个有依赖关系的 packet。

════════════════════════════════════════════════════════════════
 ★ 在这之前，一个需求恒等于一个 packet
════════════════════════════════════════════════════════════════

`build_packet_for_requirement()` 返回**单数**：role 写死 coder、
`ownsPaths` 写死 `("workspace/",)`。于是：

- 11 个角色里实际只有 1 个会被调度
- 「多 Agent 协同」体现在角色隔离与工具面派生上，**不体现在任务拆分上**
- 系统的产出上限是「一个模块」

★ 好消息：**调度层早就就绪**。`ReconcileLoop.tick()` 遍历所有非终态 packet，
  依赖看 `dep_states`，路径用 `LockTable` 互斥；
  `tests/e2e/test_abc_integration.py::test_parallel_packets_no_lock_conflict`
  已证明三个不相交路径能并行推进。

  **缺的从来不是调度，是没有人生产多个 packet。**

════════════════════════════════════════════════════════════════
 ★ 分工：模型只负责「拆成哪几件事」，其余全部确定性
════════════════════════════════════════════════════════════════

| 环节 | 由谁做 | 理由 |
|---|---|---|
| 需求 → 任务列表 | 模型 | 只有它能理解自然语言需求 |
| 任务 → 角色 | **确定性代码** | 按 kind 查表，不需要也不应该让模型猜 |
| 路径分配 | **确定性代码** | ★ 相交即违反 I1；让模型分配等于把不变量交给它守 |
| 依赖成图 | **确定性代码** | 必须无环，且 QA 必须排在 impl 之前 |
| 预算切分 | **确定性代码** | 货币计算不交给模型 |

★ 这条分界不是保守，是判据问题：**模型分配的路径没有任何测试会红**，
  而确定性分配可以在单元测试里穷举验证。
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from codentum_contracts.state import PacketId, WorkPacket

from .intake import build_packet_for_requirement, new_packet_id

__all__ = [
    "PLAN_SCHEMA",
    "PlannedTask",
    "build_packets_from_plan",
    "parse_plan",
    "plan_prompt",
]

logger = logging.getLogger(__name__)


_ROLE_BY_KIND: dict[str, str] = {
    "impl": "coder",
    "test": "qa",
    "integrate": "integrator",
    "review": "reviewer",
}
"""任务类型 → 角色。★ 查表而非让模型指定：角色决定权限与工具面，
是**安全边界**，不该由自然语言输出决定。"""

_SLUG_RE = re.compile(r"[^a-z0-9]+")

MAX_TASKS = 8
"""单个需求最多拆成几个 impl 任务。

★ 上限不是性能考虑，是**止损**：拆得越碎，每个 packet 的上下文越贫瘠，
  而集成的难度随数量超线性增长。超过上限时如实截断并记录，
  **不静默丢弃** —— 那会让需求的一部分凭空消失。
"""


@dataclass(frozen=True, slots=True)
class PlannedTask:
    """模型拆出来的一个任务（尚未变成 packet）。"""

    title: str
    detail: str
    module: str
    """模块名，用来派生互不相交的路径。"""


PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "任务标题，一句话"},
                    "detail": {"type": "string", "description": "要实现什么，具体到函数或接口"},
                    "module": {
                        "type": "string",
                        "description": "模块名，小写英文与连字符，如 frontend / api / storage。"
                        "不同任务必须用不同模块名 —— 它决定各自的文件目录。",
                    },
                },
                "required": ["title", "detail", "module"],
            },
        }
    },
    "required": ["tasks"],
}


def plan_prompt(requirement: str) -> str:
    """让模型把需求拆成任务列表。

    ★ 提示里明确三件事：模块名互不相同、每个任务自足、不要拆得太碎。
      前两条是下游的硬约束（路径不相交、上下文自足），
      第三条是成本约束。
    """

    return (
        "把下面这个需求拆成若干个可以**并行开发**的任务。\n\n"
        f"需求：{requirement}\n\n"
        "规则：\n"
        f"1. 最多 {MAX_TASKS} 个任务。宁可少而完整，不要拆得太碎 —— "
        "每个任务会由一个独立的 Agent 在独立工作区完成，拆得越碎集成越难。\n"
        "2. **每个任务的 module 必须互不相同**，它决定各自的文件目录，"
        "两个任务写同一个目录会被系统拒绝。\n"
        "3. 每个任务的 detail 要自足 —— 执行它的 Agent 看不到别的任务的内容。\n"
        "4. 只拆实现任务。测试、集成、评审由系统自动追加，不要写进来。\n\n"
        "只输出 JSON，形如："
        '{"tasks":[{"title":"…","detail":"…","module":"api"}]}'
    )


def parse_plan(raw: str) -> tuple[PlannedTask, ...]:
    """解析模型输出的任务列表。

    ★ 容忍模型在 JSON 前后加自然语言（实测常见），
      但**不容忍模块名重复** —— 那会导致路径相交，
      而路径相交是 I1 违规，必须在这里就拒绝而不是等运行时。
    """

    payload = _extract_json_object(raw)
    items = payload.get("tasks")
    if not isinstance(items, list) or not items:
        raise ValueError("拆解结果里没有任何任务")

    tasks: list[PlannedTask] = []
    seen_modules: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        detail = str(item.get("detail", "")).strip()
        module = _slugify(str(item.get("module", "")))
        if not title or not module:
            continue
        if module in seen_modules:
            # ★ 模块重名 → 路径相交 → I1 违规。
            #   这里直接改名而不是丢弃：丢弃会让需求的一部分凭空消失，
            #   而消失是**没有任何测试会红**的那种失败。
            suffix = 2
            while f"{module}-{suffix}" in seen_modules:
                suffix += 1
            logger.warning("模块名重复：%s，已改名为 %s-%d", module, module, suffix)
            module = f"{module}-{suffix}"
        seen_modules.add(module)
        tasks.append(PlannedTask(title=title, detail=detail or title, module=module))

    if not tasks:
        raise ValueError("拆解结果里没有可用的任务（缺 title 或 module）")

    if len(tasks) > MAX_TASKS:
        # ★ 截断要出声。静默丢弃会让需求的一部分消失得无影无踪。
        logger.warning("拆出 %d 个任务，超过上限 %d，已截断", len(tasks), MAX_TASKS)
        tasks = tasks[:MAX_TASKS]
    return tuple(tasks)


def build_packets_from_plan(
    tasks: tuple[PlannedTask, ...],
    *,
    requirement: str,
    model: str,
    effort: str,
    total_budget_cny: float,
    with_integration: bool = True,
    model_by_role: Mapping[str, str] | None = None,
) -> tuple[WorkPacket, ...]:
    """任务列表 → packet 列表（含 QA 前置与集成收尾）。

    产出的结构：

        [qa: 写 M 的验收测试] ──→ [coder: 实现 M] ──┐
        [qa: 写 N 的验收测试] ──→ [coder: 实现 N] ──┼──→ [integrator: 集成]
                                                    ┘

    ★ `model_by_role` 必须传：每个角色在 RoleSpec 里声明了自己的
      `modelPolicy.defaultModel`，而 qa / reviewer 还声明了
      `mustDifferFrom: [coder]`。给所有 packet 用同一个模型会被准入
      以 `MODEL_ISOLATION` 拒绝 —— **同一模型既出题又做题，盲区重叠，
      评审失效**，而那正是 QA 前置要修的问题的另一面。

      ★ 第一版就是这么写的（所有 packet 同一个 model），
        端到端跑时被准入当场拦下。**不变量拦住了实现它的人。**

    ★ 三条硬约束，全部在这里确定性地保证：

    1. **路径两两不相交** —— 每个模块占 `workspace/<module>/`，
       QA 占 `workspace/<module>/tests/`。相交即 I1 违规。
    2. **QA 排在 impl 之前** —— impl 依赖 test，先出题后做题。
       这是对「判据套利」的结构性修复：验收标准不能由执行者自己写。
    3. **依赖图无环** —— 拓扑是 test → impl → integrate 三层，天然无环。
    """

    if not tasks:
        raise ValueError("任务列表为空")
    if total_budget_cny <= 0:
        raise ValueError("total_budget_cny 必须为正")

    # ★ 预算按 packet 数均分。集成 packet 也要算进去 ——
    #   漏算它会导致最后一步没钱可花，而那时前面的钱已经花掉了。
    packet_count = len(tasks) * 2 + (1 if with_integration else 0)
    per_packet = total_budget_cny / packet_count

    packets: list[WorkPacket] = []
    impl_ids: list[PacketId] = []

    for task in tasks:
        # ★ 两者必须**同级**，不能是父子关系。
        #
        #   第一版写的是 workspace/<m>/ 与 workspace/<m>/tests/ ——
        #   后者是前者的子路径，于是 impl 的 ownsPaths 覆盖了 QA 的目录：
        #   **执行者能改验收标准**，判据可被套利，而这正是要修的那个问题。
        #   测试当场抓住了它。
        module_root = f"workspace/{task.module}/src/"
        tests_root = f"workspace/{task.module}/tests/"

        test_id = new_packet_id()
        impl_id = new_packet_id()

        # ── ① QA 先出题 ────────────────────────────────
        packets.append(
            _retag(
                build_packet_for_requirement(
                    packet_id=test_id,
                    requirement=_test_brief(task, requirement),
                    owns_paths=(tests_root,),
                    reads_paths=(),
                    model=model,
                    effort=effort,
                    budget_cny=per_packet,
                    acceptance_author="reviewer",
                ),
                kind="test",
                role="qa",
                model=_role_model("qa", model_by_role, model),
            )
        )

        # ── ② coder 实现，依赖 QA ──────────────────────
        packets.append(
            _retag(
                build_packet_for_requirement(
                    packet_id=impl_id,
                    requirement=_impl_brief(task, requirement),
                    owns_paths=(module_root,),
                    # ★ impl 读得到 tests/，但写不了 —— 它要看验收标准，
                    #   却不能改验收标准。这是 I2 在路径层面的落实。
                    reads_paths=(tests_root,),
                    model=model,
                    effort=effort,
                    budget_cny=per_packet,
                    acceptance_author="qa",
                ),
                kind="impl",
                role="coder",
                model=_role_model("coder", model_by_role, model),
                deps=(test_id,),
            )
        )
        impl_ids.append(impl_id)

    if with_integration:
        # ── ③ 集成：等所有 impl 完成 ──────────────────
        #
        # ★ 没有这一步，各模块的测试全绿但从未在同一个进程里跑过 ——
        #   正是「各段都对、合起来不通」。
        packets.append(
            _retag(
                build_packet_for_requirement(
                    packet_id=new_packet_id(),
                    requirement=_integration_brief(tasks, requirement),
                    owns_paths=("workspace/",),
                    reads_paths=(),
                    model=model,
                    effort=effort,
                    budget_cny=per_packet,
                    acceptance_author="reviewer",
                ),
                kind="integrate",
                role="integrator",
                model=_role_model("integrator", model_by_role, model),
                deps=tuple(impl_ids),
            )
        )

    return tuple(packets)


# ── 内部 ────────────────────────────────────────────────


def _role_model(role: str, table: Mapping[str, str] | None, fallback: str) -> str:
    """取角色自己的默认模型。

    ★ 不能所有角色共用一个模型：qa / reviewer 声明了
      `mustDifferFrom: [coder]`，共用会被准入以 MODEL_ISOLATION 拒绝。
      **这条不变量的意义正是「同一模型既写又审等于没审」。**
    """

    return (table or {}).get(role) or fallback


def _retag(
    packet: WorkPacket,
    *,
    kind: str,
    role: str,
    model: str | None = None,
    deps: tuple[PacketId, ...] = (),
) -> WorkPacket:
    """改写 kind / role / deps。

    ★ 用 `model_copy` 而不是重新构造：`build_packet_for_requirement`
      里那些关于 routing、budget、acceptance 的注释所记录的坑
      （model="default" 不存在、dict 构造绕过 mypy 等）都还成立，
      重新构造一遍等于把它们重踩一次。
    """

    update: dict[str, Any] = {"kind": kind, "role": role, "deps": deps}
    if model is not None and packet.routing is not None:
        update["routing"] = packet.routing.model_copy(update={"model": model})
    return packet.model_copy(update=update)


def _slugify(raw: str) -> str:
    slug = _SLUG_RE.sub("-", raw.strip().lower()).strip("-")
    return slug[:40]


def _extract_json_object(raw: str) -> dict[str, Any]:
    """从模型输出里取出第一个 JSON 对象。

    ★ 模型很常见地在 JSON 前后加自然语言（"好的，这是拆解结果："）。
      整串 `json.loads` 必然失败，而中间那个对象其实是好的。
      这与工具调用参数的处理是同一条经验。
    """

    text = raw.strip()
    if text.startswith("```"):
        # 去掉 markdown 代码围栏
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    if start < 0:
        raise ValueError(f"拆解结果里没有 JSON：{raw[:200]!r}")
    decoder = json.JSONDecoder(strict=False)
    try:
        payload, _ = decoder.raw_decode(text[start:])
    except json.JSONDecodeError as exc:
        raise ValueError(f"拆解结果不是合法 JSON：{raw[:200]!r}") from exc
    if not isinstance(payload, dict):
        raise ValueError("拆解结果的顶层必须是对象")
    return payload


def _impl_brief(task: PlannedTask, requirement: str) -> str:
    return (
        f"【总需求】{requirement}\n\n"
        f"【你负责的部分】{task.title}\n{task.detail}\n\n"
        f"把代码写在 workspace/{task.module}/ 下。\n"
        f"★ 验收测试已经由 QA 写在 workspace/{task.module}/tests/ 下 —— "
        "先读它，你的实现必须让它通过。**你不能修改测试文件。**"
    )


def _test_brief(task: PlannedTask, requirement: str) -> str:
    return (
        f"【总需求】{requirement}\n\n"
        f"【你负责出题的部分】{task.title}\n{task.detail}\n\n"
        f"在 workspace/{task.module}/tests/ 下写 pytest 验收测试。\n"
        "★ 你是**出题方**，不是实现方：\n"
        f"- 假设实现将位于 workspace/{task.module}/，按它**应有的**接口写测试\n"
        "- 测试必须真正调用被测代码并断言其返回值；`assert True` 会被系统拒绝\n"
        "- 不要写实现，只写测试"
    )


def _integration_brief(tasks: tuple[PlannedTask, ...], requirement: str) -> str:
    modules = "、".join(f"workspace/{t.module}/" for t in tasks)
    return (
        f"【总需求】{requirement}\n\n"
        f"【你负责】把已完成的各模块集成为一个可运行的整体。\n"
        f"已完成的模块：{modules}\n\n"
        "★ 各模块的测试各自都通过了，但它们**从未在同一个进程里跑过**。\n"
        "要做的：\n"
        "- 跑一次全量测试 `python -m pytest workspace -q`，修掉集成层面的问题\n"
        "- 补上把各模块连起来的入口（如 workspace/main.py）\n"
        "- 不要重写各模块的内部实现"
    )
