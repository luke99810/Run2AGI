#!/usr/bin/env python
"""gen_types —— 从 JSON Schema 生成两套类型

    packages/contracts/schemas/*.json
              ├──→ packages/contracts/python/codentum_contracts/state.py   (Pydantic)
              └──→ packages/contracts/typescript/state.ts                  (供 desktop)

════════════════════════════════════════════════════════════════
 为什么两侧都生成
════════════════════════════════════════════════════════════════

ADR-0003 把核心引擎改成 Python，桌面端仍是 TypeScript ——
于是出现了一条跨语言边界，而跨语言边界是契约漂移的高发区。

★ 唯一有效的缓解是：两侧都是生成物，谁都不手写。
  schema 是唯一真源，`gen:check` 同时校验两套输出。
  这样漂移在机制上被堵住，而不是靠"记得同步"。

════════════════════════════════════════════════════════════════
 三条实现约束
════════════════════════════════════════════════════════════════

1. ★ 输出必须【确定性】：同 schema 必须逐字节生成同样的文件。
   所以没有时间戳、没有随机、不依赖 glob 顺序 ——
   PLAN 显式列出文件与顺序，漏列的 schema 会报错而不是被静默排在后面。

2. ★ 不做命名转换（snake_case ↔ camelCase）。
   转换规则本身就是漂移的来源 —— schema 里写什么，两侧就都是什么。
   schema 字段名一律 camelCase。

3. ★ 只生成类型，不生成运行时逻辑。

用法：
    python scripts/gen_types.py            写入两侧
    python scripts/gen_types.py --check    只校验一致性（CI 用）
"""

from __future__ import annotations

import json
import keyword
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from lib.console import setup_console  # noqa: E402
from lib.schema import Schema, deref, load_schemas  # noqa: E402

setup_console()

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = ROOT / "packages" / "contracts" / "schemas"
OUT_PY = ROOT / "packages" / "contracts" / "python" / "codentum_contracts" / "state.py"
OUT_TS = ROOT / "packages" / "contracts" / "typescript" / "state.ts"

# ★ 显式列出要生成的 schema 及顺序。
#   不用目录排序 —— 那会让「新增一个 schema」静默改变输出顺序，
#   进而让 gen:check 在一个与本次改动无关的地方红。
PLAN: list[dict[str, Any]] = [
    {"file": "identifiers.schema.json", "section": "标识", "root": False},
    {"file": "workpacket.schema.json", "section": "WorkPacket", "root": True},
    {"file": "budget.schema.json", "section": "预算 —— ★ 一律货币，不用 token", "root": True},
    {"file": "graph.schema.json", "section": "依赖图 · 所有权图", "root": True},
    {"file": "knowledge.schema.json", "section": "溯源图 · 知识图", "root": True},
    {"file": "evidence.schema.json", "section": "证据 —— I6", "root": True},
    {"file": "decision.schema.json", "section": "决策日志", "root": True},
    {"file": "rolespec.schema.json", "section": "RoleSpec", "root": True},
]


def fail(msg: str) -> None:
    print(f"\n✗ gen_types 失败\n\n{msg}\n", file=sys.stderr)
    raise SystemExit(1)


schemas = load_schemas(SCHEMA_DIR)

_planned = {p["file"] for p in PLAN}
_missing = sorted(set(schemas) - _planned)
if _missing:
    fail(
        "以下 schema 没有列进 PLAN：\n  "
        + "\n  ".join(_missing)
        + "\n\n★ 这是刻意报错，不是自动兜底。新增 schema 时请到 scripts/gen_types.py 的 PLAN 里\n"
        "  显式指定它的位置与分节标题 —— 否则输出顺序会随文件名变化，\n"
        "  让 gen:check 在与本次改动无关的地方红。"
    )
for p in PLAN:
    if p["file"] not in schemas:
        fail(f"PLAN 里列的 {p['file']} 不存在于 {SCHEMA_DIR}")


# ══════════════════════════════════════════════════════════════
#  发射器基类
# ══════════════════════════════════════════════════════════════


class Emitter:
    """把 schema 翻译成某种语言的类型声明。子类只需实现四个 render_*。"""

    def __init__(self) -> None:
        self.decls: list[tuple[str, str]] = []
        self.seen: set[str] = set()

    # ── 子类实现 ──────────────────────────────────────────
    def render_brand(self, name: str, node: Schema) -> str: ...
    def render_alias(self, name: str, body: str, node: Schema) -> str: ...
    def render_object(self, name: str, node: Schema, file: str, desc: str | None) -> str: ...
    def scalar(self, t: str) -> str: ...
    def array(self, item: str) -> str: ...
    def record(self, value: str) -> str: ...
    def literal(self, values: list[Any]) -> str: ...

    # ── 通用 ──────────────────────────────────────────────
    def type_of(self, node: Any, file: str) -> str:
        node, file = self._unwrap(node, file)
        if not isinstance(node, dict):
            fail(f"无法翻译的 schema 节点：{node!r}")

        if "oneOf" in node:
            return self.union([self.type_of(n, file) for n in node["oneOf"]])

        brand = node.get("x-brand")
        if brand:
            if brand not in self.seen:
                self.seen.add(brand)
                self.decls.append((brand, self.render_brand(brand, node)))
            return brand

        name = node.get("x-typeName")
        if name:
            if name not in self.seen:
                self.seen.add(name)
                self.decls.append((name, self._render_named(name, node, file)))
            return name

        return self.raw(node, file)

    def _unwrap(self, node: Any, file: str) -> tuple[Any, str]:
        """展开 $ref / 单元素 allOf，但保留 x-brand / x-typeName 标记。"""
        if isinstance(node, dict) and ("x-brand" in node or "x-typeName" in node):
            return node, file
        if isinstance(node, dict) and ("$ref" in node or "allOf" in node):
            return deref(node, file, schemas)
        return node, file

    def _render_named(self, name: str, node: Schema, file: str) -> str:
        if "properties" in node:
            return self.render_object(name, node, file, node.get("description"))
        return self.render_alias(name, self.raw(node, file), node)

    def raw(self, node: Schema, file: str) -> str:
        if "const" in node:
            return self.literal([node["const"]])
        if "enum" in node:
            return self.literal(node["enum"])
        if node.get("type") == "array":
            return self.array(self.type_of(node["items"], file) if "items" in node else self.scalar("unknown"))
        if node.get("type") == "object" or "properties" in node:
            if "properties" not in node and isinstance(node.get("additionalProperties"), dict):
                return self.record(self.type_of(node["additionalProperties"], file))
            return self.render_object("", node, file, None)  # 内联对象（本项目未用到）
        if node.get("type") == "string" and node.get("pattern"):
            return self.constrained_str(node["pattern"])
        return self.scalar(node.get("type", "unknown"))

    def constrained_str(self, pattern: str) -> str:
        """带 pattern 的字符串。TS 侧表达不了，Python 侧能在运行时校验。"""
        return self.scalar("string")

    def union(self, parts: list[str]) -> str:
        return " | ".join(parts)

    def doc_of(self, node: Any, file: str) -> str | None:
        """外层 description 优先；没有就取 $ref 目标的。"""
        if isinstance(node, dict) and node.get("description"):
            return node["description"]
        try:
            target, _ = deref(node, file, schemas)
        except ValueError:
            return None
        return target.get("description") if isinstance(target, dict) else None

    def build(self) -> str:
        chunks = [self.header()]
        for p in PLAN:
            schema = schemas[p["file"]]
            before = len(self.decls)
            root_code = None
            if p["root"]:
                title = schema.get("title")
                if not title:
                    fail(f"{p['file']} 缺 title —— 根类型名从它来")
                self.seen.add(title)
                root_code = self._render_named(title, schema, p["file"])
            else:
                for d in schema.get("$defs", {}).values():
                    self.type_of(d, p["file"])
            produced = [c for _, c in self.decls[before:]]
            body = self.decl_sep.join(x for x in ([root_code] + produced) if x)
            if body:
                chunks.append(f"{self.banner(p['section'])}\n\n{body}")
        return self.decl_sep.join(chunks) + "\n"

    decl_sep = "\n\n"

    def header(self) -> str: ...
    def banner(self, title: str) -> str: ...


# ══════════════════════════════════════════════════════════════
#  Python / Pydantic
# ══════════════════════════════════════════════════════════════

PY_SCALARS = {"string": "str", "number": "float", "integer": "int", "boolean": "bool", "null": "None"}


class PyEmitter(Emitter):
    decl_sep = "\n\n\n"  # PEP 8：顶层定义之间空两行

    def header(self) -> str:
        return '''"""状态数据类型（Pydantic 模型）

⚠️ 本文件由 `python scripts/gen_types.py` 从 packages/contracts/schemas/*.json 生成。
   【不要手改】—— 下次生成会覆盖。

要改数据形状：改 schema → 重跑生成 → 提交生成结果。
CI 里的 `gen:check` 会验证仓库内容与生成物一致。

★ 全部模型 frozen=True、extra="forbid"，数组用 tuple：
  状态是不可变的。四张图里只有所有权图需要并发控制，
  而"不可变 + 显式替换"让另外三张图连锁都不需要。

★★ 写回 .codentum/ 时必须用 dump_state()，不要直接 model_dump()。
   schema 里的 `from` 撞上 Python 关键字，属性名是 `from_`，靠 alias 映射回去。
   直接 model_dump() 会把 "from_" 写进 JSON —— 文件仍是合法 JSON，
   但 schema 校验会失败，而失败点离出错点很远。

真源：packages/contracts/schemas/*.json
生成器：scripts/gen_types.py
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal, NewType

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class _Base(BaseModel):
    """所有状态模型的基类。

    extra="forbid"      对应 schema 的 additionalProperties: false —— 多一个字段就报错。
    frozen=True         状态不可变，改动靠 model_copy(update=...) 显式产生新值。
    populate_by_name    允许用 Python 属性名构造（from_=...），也允许用 schema 名（from=...）。
    """

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


def dump_state(model: BaseModel) -> dict[str, Any]:
    """把模型序列化成【符合 schema】的 dict。写 .codentum/ 一律走这个。

    三个参数都是必须的，少一个就会写出不合 schema 的文件：

    ★ by_alias=True     把 from_ 还原成 from。
    ★ exclude_none=True schema 里可选字段的语义是"没有这个键"，不是"键存在但值为 null"
                        —— 这个区分正是 TS 侧 exactOptionalPropertyTypes 守的东西。
    ★ mode="json"       tuple → list。不加这个，dump 出来的是 Python 结构而非 JSON 结构，
                        json.dumps 能写出去，但任何逐字段比对/往返校验都会失败。
    """
    return model.model_dump(mode="json", by_alias=True, exclude_none=True)'''

    def banner(self, title: str) -> str:
        return f"# {'═' * 60}\n#  {title}\n# {'═' * 60}"

    def scalar(self, t: str) -> str:
        return PY_SCALARS.get(t, "object")

    def array(self, item: str) -> str:
        return f"tuple[{item}, ...]"

    def record(self, value: str) -> str:
        return f"Mapping[str, {value}]"

    def literal(self, values: list[Any]) -> str:
        return "Literal[" + ", ".join(json.dumps(v, ensure_ascii=False) for v in values) + "]"

    def constrained_str(self, pattern: str) -> str:
        return f'Annotated[str, StringConstraints(pattern=r"{pattern}")]'

    def render_brand(self, name: str, node: Schema) -> str:
        # ★ NewType 包 Annotated —— 两个性质都要，缺一个都不行：
        #     NewType    mypy 下 PacketId 与 EvidenceRef 不可互换（对应 TS 的品牌类型）
        #     Annotated  Pydantic 运行时校验 pattern
        #   只用 NewType 会丢掉 pattern，而"从磁盘读到一个畸形 id"正是最现实的失败模式；
        #   只用 Annotated 会丢掉名义类型，传错 id 就查不出来。
        doc = f"\n{_pydoc(node['description'], '')}" if node.get("description") else ""
        base = self.constrained_str(node["pattern"]) if node.get("pattern") else "str"
        return f'{name} = NewType("{name}", {base}){doc}'

    def render_alias(self, name: str, body: str, node: Schema) -> str:
        doc = f"\n{_pydoc(node['description'], '')}" if node.get("description") else ""
        return f"{name} = {body}{doc}"

    def render_object(self, name: str, node: Schema, file: str, desc: str | None) -> str:
        required = set(node.get("required", []))
        lines = [f"class {name}(_Base):"]
        if desc:
            lines.append(_pydoc(desc, "    "))
        for key, sub in node.get("properties", {}).items():
            t = self.type_of(sub, file)
            d = self.doc_of(sub, file)
            # ★ schema 里的 from / to 等字段撞上 Python 关键字。
            #   不改 schema（那会改动契约、破坏固件与 TS 侧），而是加 Field(alias=...)：
            #   线上格式仍是 "from"，Python 属性叫 "from_"。
            attr = f"{key}_" if keyword.iskeyword(key) else key
            alias = f'alias="{key}"' if attr != key else ""
            if key in required:
                default = f" = Field({alias})" if alias else ""
            else:
                default = f" = Field(default=None, {alias})" if alias else " = None"
                t = f"{t} | None"
            lines.append(f"    {attr}: {t}{default}")
            if d:
                lines.append(_pydoc(d, "    "))
        return "\n".join(lines)


def _pydoc(text: str, indent: str) -> str:
    lines = str(text).split("\n")
    if len(lines) == 1:
        return f'{indent}"""{lines[0]}"""'
    body = "\n".join(f"{indent}{ln}".rstrip() for ln in lines)
    return f'{indent}"""\n{body}\n{indent}"""'


# ══════════════════════════════════════════════════════════════
#  TypeScript
# ══════════════════════════════════════════════════════════════

TS_SCALARS = {"string": "string", "number": "number", "integer": "number", "boolean": "boolean", "null": "null"}


class TsEmitter(Emitter):
    def header(self) -> str:
        return """/**
 * 状态数据类型
 *
 * ⚠️ 本文件由 `python scripts/gen_types.py` 从 packages/contracts/schemas/*.json 生成。
 *    【不要手改】—— 下次生成会覆盖。
 *
 * ★ 与 packages/contracts/python/codentum_contracts/state.py 同源。
 *   两侧都是生成物，谁都不手写 —— 这是跨语言边界唯一有效的防漂移手段。
 *
 * 真源：packages/contracts/schemas/*.json
 * 生成器：scripts/gen_types.py
 */"""

    def banner(self, title: str) -> str:
        return f"// {'═' * 58}\n//  {title}\n// {'═' * 58}"

    def scalar(self, t: str) -> str:
        return TS_SCALARS.get(t, "unknown")

    def array(self, item: str) -> str:
        needs_parens = ("|" in item or "&" in item) and not item.startswith("(")
        return f"readonly ({item})[]" if needs_parens else f"readonly {item}[]"

    def record(self, value: str) -> str:
        return f"Readonly<Record<string, {value}>>"

    def literal(self, values: list[Any]) -> str:
        return " | ".join(json.dumps(v, ensure_ascii=False) for v in values)

    def render_brand(self, name: str, node: Schema) -> str:
        d = node.get("description")
        return f"{_tsdoc(d, '')}export type {name} = string & {{ readonly __brand: '{name}' }};"

    def render_alias(self, name: str, body: str, node: Schema) -> str:
        return f"{_tsdoc(node.get('description'), '')}export type {name} = {body};"

    def render_object(self, name: str, node: Schema, file: str, desc: str | None) -> str:
        required = set(node.get("required", []))
        lines = [f"{_tsdoc(desc, '')}export interface {name} {{"]
        for key, sub in node.get("properties", {}).items():
            d = self.doc_of(sub, file)
            if d:
                lines.append(_tsdoc(d, "  ").rstrip("\n"))
            opt = "" if key in required else "?"
            lines.append(f"  readonly {key}{opt}: {self.type_of(sub, file)};")
        lines.append("}")
        return "\n".join(lines)


def _tsdoc(text: str | None, indent: str) -> str:
    if not text:
        return ""
    lines = str(text).split("\n")
    if len(lines) == 1 and len(lines[0]) + len(indent) < 92:
        return f"{indent}/** {lines[0]} */\n"
    body = "\n".join(f"{indent} * {ln}".rstrip() for ln in lines)
    return f"{indent}/**\n{body}\n{indent} */\n"


# ══════════════════════════════════════════════════════════════
#  入口
# ══════════════════════════════════════════════════════════════


def main() -> None:
    outputs = [(OUT_PY, PyEmitter().build()), (OUT_TS, TsEmitter().build())]

    if "--check" in sys.argv:
        bad = []
        for path, content in outputs:
            current = path.read_text(encoding="utf-8") if path.exists() else ""
            if current != content:
                bad.append(path.relative_to(ROOT).as_posix())
        if bad:
            print(
                "\n✗ 生成物与 schema 不一致：\n  " + "\n  ".join(bad) + "\n\n"
                "  跑 `python scripts/gen_types.py` 重新生成并提交。\n"
                "  ★ 不要反过来改生成物 —— 真源是 schemas/。\n",
                file=sys.stderr,
            )
            raise SystemExit(1)
        print(f"✓ 两侧生成物与 schema 一致（{len(outputs)} 个文件）")
        return

    total = 0
    for path, content in outputs:
        path.parent.mkdir(parents=True, exist_ok=True)
        # ★ newline="\n" 是必须的：默认会把换行翻成 os.linesep，
        #   于是 Windows 写出 CRLF、Linux 写出 LF —— 同一份 schema 在两台机器上
        #   生成出逐字节不同的文件，而"输出确定性"正是这个生成器的第一条约束。
        path.write_text(content, encoding="utf-8", newline="\n")
        total += 1
    py_types = len(PyEmitter().build().split("\nclass ")) - 1
    print(f"✓ 已生成 Python + TypeScript 两套类型（{len(PLAN)} 份 schema，{py_types} 个模型）")


if __name__ == "__main__":
    main()
