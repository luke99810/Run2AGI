"""把一句需求变成一个 WorkPacket，并把需求原文送进模型的上下文。

════════════════════════════════════════════════════════════════
 ★ 这里没有 Planner，也不假装有
════════════════════════════════════════════════════════════════

设计文档里，需求 → 多个 packet 的分解由 **Planner** 做，问题定义由
**Intake** 做。两者都还没有实现（`packages/control-plane/` 下没有这两个
模块，`RoleId` 里的 `intake` / `planner` 目前只是两个字面量）。

所以这个模块做的是**一个需求 → 一个 packet**，不是分解。写死一套「登录
模块 / 数据层 / 测试」的假拆分会让演示看起来更像那么回事，但那正是本项目
一直在抓的那类东西：**看得见的产物是真的，产生它的过程是编的。**

真正的分解要等 Planner。在此之前，这里的行为必须一眼可辨：一进一出。


════════════════════════════════════════════════════════════════
 ★ 需求原文走 ContextBundle，不走 WorkPacket
════════════════════════════════════════════════════════════════

08-10 首次真实模型跑通时暴露：`WorkPacket` **没有任务描述字段**，实际发给
模型的 prompt 里 `Visible Context: (none)`，于是模型回了一份 blocker 报告
说「我看不到要做什么」—— 模型是对的。

契约已于 2026-08-02 冻结，加字段需三人同意 + ADR + 变更窗口，不由一个人
直接改（记为待发起 ADR-0007）。

但这不代表只能干等：`LocalWorkerRuntime` 本来就有 `context_loader` 这个
注入点（`Callable[[SpawnRequest, RoleSpec], tuple[ContextCandidate, ...]]`），
需求原文经它进入 ContextBundle，是**设计里本来就有的那条路**，不是绕过。

所以这里的分工是：
  - 需求原文落盘到 `.codentum/requirements/<packetId>.json`（引擎自己的记录）
  - `requirement_context_loader()` 按 packet id 取回，作为 `required=True`
    的上下文候选交给 Harness
  - 契约层的缺陷仍然存在，仍然要走 ADR —— 这里解决的是「模型收不到任务」
    这个可运行性问题，不是那个契约缺陷本身。
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from codentum_contracts.state import (
    Acceptance,
    BudgetGrant,
    ModelRouting,
    PacketId,
    Provenance,
    WorkPacket,
)

__all__ = [
    "DEFAULT_PACKET_BUDGET_CNY",
    "RequirementRecord",
    "RequirementStore",
    "build_packet_for_requirement",
    "choose_acceptance_author",
    "new_packet_id",
]

logger = logging.getLogger(__name__)

# ★ 一个 packet 的默认预算。写成常量而不是散在代码里的字面量，是因为它会
#   直接决定「跑到一半被预算掐掉」。桌面端提交时可以在 payload 里覆盖它。
DEFAULT_PACKET_BUDGET_CNY = 1.0

# ★ 需求提交时没有人写验收标准 —— 提交的是一句话，不是 ACCEPTANCE.md。
#   按 I2「验收可判定」，这里必须如实标成 manual + 由人判定，
#   不能凭空塞一条 `pytest` 假装它机器可判定。
_PLACEHOLDER_PREDICATE = (
    "operator-review: 需求由操作者直接提交，未附机器可判定的验收标准；"
    "此条为引擎在 Intake 缺位时生成的占位验收，需人工判定"
)

DEFAULT_TEST_PREDICATE = "python -m pytest workspace -q"
"""默认的**可执行**验收谓词。

★ 为什么要有它：`kind: manual` 的占位验收永远不会被执行，于是
  「产生了一条真实证据」就等于「验收通过」。2026-08-12 实测：模型只写了
  规格要求的两个文件中的一个，packet 照样 accepted。

★ 为什么是跑测试而不是别的：需求是自由文本，机器判不了「做得对不对」；
  但「你自己写的测试跑不跑得过」是机器能判的，而且它把举证责任
  推回给了执行者 —— 想通过验收，就得写出能跑的测试。

★ 仍然不完美：模型可以写一个恒真的测试。那属于 §十五 推论 2
  「可判定不等于判得出差别」的下一层，需要 QA 角色独立出题才能解决，
  而 QA 目前还没有独立的 packet。**这个缺口记在案，不假装它不存在。**
"""

# ★ 谁「写」了这条占位验收？
#
#   概念上是 Intake —— 问题定义端。但 `check_role_exists` 会拒绝任何
#   不在已加载 RoleSpec 里的 authoredBy，而 `intake` 正是 B 还没写的那
#   8 份之一（当前只有 coder / qa / reviewer）。
#
#   这条准入规则是对的，不该为了让引擎跑起来去削弱它：一个指向不存在角色
#   的审计记录，比没有记录更糟。
#
#   所以按优先级挑一个**确实存在**的角色，并且让缺位这件事在日志里留痕。
#   等 B 补上 intake 的 RoleSpec，这里会自动切回 intake ——
#   `test_acceptance_author_prefers_intake_once_the_rolespec_exists` 钉着这个。
_ACCEPTANCE_AUTHOR_PREFERENCE = ("intake", "qa", "reviewer")


def new_packet_id() -> PacketId:
    """生成合法的 PacketId。

    契约的 pattern 是 `^wp-[0-9a-z]{6,}$` —— 大写字母和连字符都会被拒，
    所以不能直接用 `uuid4()` 的字符串形式。
    """

    return PacketId(f"wp-{uuid.uuid4().hex[:12]}")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class RequirementRecord:
    """一次需求提交的原始记录。"""

    __slots__ = ("packet_id", "text", "submitted_at", "command_id", "payload")

    def __init__(
        self,
        *,
        packet_id: str,
        text: str,
        submitted_at: str,
        command_id: str,
        payload: dict[str, Any],
    ) -> None:
        self.packet_id = packet_id
        self.text = text
        self.submitted_at = submitted_at
        self.command_id = command_id
        self.payload = payload

    def to_json(self) -> dict[str, Any]:
        return {
            "packetId": self.packet_id,
            "text": self.text,
            "submittedAt": self.submitted_at,
            "commandId": self.command_id,
            # ★ 整份 payload 原样存档。桌面端会往里塞 taskId / taskHistory /
            #   attachments / connectivityMode 等字段（见 C 的任务书），
            #   引擎现在还不消费它们 —— 但**丢掉**和**存着没用**是两回事：
            #   丢掉之后就再也无法回答「当时用户到底提交了什么」。
            "payload": self.payload,
        }


class RequirementStore:
    """`.codentum/requirements/` 的读写。

    ★ 单独一个目录，不塞进 `knowledge/`。桌面端的 `directory-state-source`
      会按 `isKnowledgeFile` 解析 `knowledge/` 下的每个文件，塞进去会让它
      把整份快照判为 incoherent。不在它扫描清单里的目录会被忽略，是安全的。
    """

    def __init__(self, state_dir: Path | str) -> None:
        self._root = Path(state_dir) / "requirements"

    def save(self, record: RequirementRecord) -> Path:
        self._root.mkdir(parents=True, exist_ok=True)
        path = self._root / f"{record.packet_id}.json"
        path.write_text(
            json.dumps(record.to_json(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def text_for(self, packet_id: str) -> str | None:
        path = self._root / f"{packet_id}.json"
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        text = raw.get("text")
        return text if isinstance(text, str) and text else None


def choose_acceptance_author(
    available_roles: Sequence[str], *, packet_role: str
) -> str:
    """挑一个存在的角色来署名占位验收。

    ★ 返回值必须满足两条硬约束，否则准入会拒：
      1. 在已加载的 RoleSpec 里（`check_role_exists`）
      2. != packet 自己的 role（`check_self_review`，自己给自己定验收即作弊）
    """

    roles = [r for r in available_roles if r != packet_role]
    for preferred in _ACCEPTANCE_AUTHOR_PREFERENCE:
        if preferred in roles:
            if preferred != _ACCEPTANCE_AUTHOR_PREFERENCE[0]:
                logger.warning(
                    "RoleSpec 里没有 %r，占位验收改由 %r 署名。"
                    "补上 intake 的 RoleSpec 后会自动切回。",
                    _ACCEPTANCE_AUTHOR_PREFERENCE[0],
                    preferred,
                )
            return preferred
    if roles:
        logger.warning("首选角色都不在 RoleSpec 里，占位验收改由 %r 署名。", roles[0])
        return roles[0]
    raise ValueError(
        f"没有任何可用于署名验收的角色（已加载：{sorted(available_roles)}，"
        f"packet.role={packet_role!r}）。至少需要一份 packet.role 之外的 RoleSpec。"
    )


def build_packet_for_requirement(
    *,
    packet_id: PacketId,
    requirement: str,
    owns_paths: Sequence[str],
    reads_paths: Sequence[str],
    model: str,
    effort: str,
    budget_cny: float,
    acceptance_author: str,
    executable_acceptance: bool = True,
) -> WorkPacket:
    """一个需求 → 一个 coder packet。

    ★ role 固定为 coder：现在只有 coder / qa / reviewer 三份 RoleSpec，
      而其中只有 coder 的 modelPolicy 经过实测（08-09 B 的百炼验证）。
      角色选择应当由 Planner 依据任务性质决定 —— 写死在这里是**当前的
      实现边界**，不是设计。
    """

    if not requirement.strip():
        raise ValueError("requirement 不能为空白")
    if budget_cny <= 0:
        raise ValueError("budget_cny 必须为正")

    return WorkPacket(
        id=packet_id,
        kind="impl",
        state="pending",
        role="coder",
        ownsPaths=tuple(owns_paths),
        readsPaths=tuple(reads_paths),
        deps=(),
        # ★ 这四个字段用模型实例构造，不用 dict。
        #   `loop.py` 那边是 dict（load_state 从 JSON 读进来，那里合理），
        #   但在**构造**路径上用 dict 会让 mypy 看不见字段错误 ——
        #   写错一个字段名的后果是 pydantic 在运行时才炸，而它炸的位置
        #   离写错的位置很远（08-10 已经踩过一次「报错位置不等于出错位置」）。
        acceptance=(
            # ★ 可执行谓词优先：只有 kind=test 会被门禁真的跑一遍。
            #   用 manual 的话，「有证据」就等于「验收通过」——
            #   那正是 2026-08-12 那个虚假 accepted 的来源。
            Acceptance(
                kind="test",
                predicate=DEFAULT_TEST_PREDICATE,
                threshold=None,
                authoredBy=acceptance_author,  # type: ignore[arg-type]
            )
            if executable_acceptance
            else Acceptance(
                kind="manual",
                predicate=_PLACEHOLDER_PREDICATE,
                threshold=None,
                authoredBy=acceptance_author,  # type: ignore[arg-type]
            )
        ),
        budget=BudgetGrant(
            currency="CNY",
            limitCny=budget_cny,
            spentCny=0.0,
            degradationChain=("drop_semantic",),
        ),
        # ★ 必须显式填 routing。不填的话 A 的 `_build_spawn_request` 会退回
        #   `model="default"`，而 "default" 在百炼那边不存在 —— 现象是
        #   provider 报错，看上去像「模型连不上」，真因却是路由没填。
        #   这个坑 08-10 的 e2e 已经踩过一次。
        routing=ModelRouting(model=model, effort=effort, batch=None),  # type: ignore[arg-type]
        attempts=0,
        evidence=(),
        provenance=Provenance(
            createdBy="intake",
            createdAt=_now_iso(),
            parent=None,
        ),
    )
