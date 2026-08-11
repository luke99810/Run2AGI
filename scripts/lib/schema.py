"""JSON Schema 的载入、$ref 解析与校验。

★ 零第三方依赖，刻意不引 jsonschema/pydantic。
  校验类脚本要在 `pip install` 之前就能跑 —— 否则第一天拉下仓库的人
  没法确认自己拿到的是一份自洽的契约。

支持的 JSON Schema 子集（够本项目用，不够时【报错而不是静默忽略】）：
    type / const / enum / pattern / required / properties /
    additionalProperties(false | schema) / items / minItems /
    minimum / maximum / exclusiveMinimum / oneOf / allOf(单元素) / $ref / $defs

使用者：scripts/gen_types.py · scripts/validate_fixtures.py · scripts/gen_contract_tests.py
"""

from __future__ import annotations

from collections.abc import Callable

import json
import re
from pathlib import Path
from typing import Any

Schema = dict[str, Any]


def load_schemas(schema_dir: Path) -> dict[str, Schema]:
    """载入 schema 目录，返回 文件名 → schema。"""
    out: dict[str, Schema] = {}
    for p in sorted(schema_dir.glob("*.schema.json")):
        out[p.name] = json.loads(p.read_text(encoding="utf-8"))
    return out


def deref(node: Any, file: str, schemas: dict[str, Schema]) -> tuple[Any, str]:
    """展开 $ref 与单元素 allOf，返回 (最终节点, 所属文件)。"""
    cur, cur_file = node, file
    for _ in range(32):
        if not isinstance(cur, dict):
            return cur, cur_file
        all_of = cur.get("allOf")
        if isinstance(all_of, list) and len(all_of) == 1:
            cur = all_of[0]
            continue
        ref = cur.get("$ref")
        if not ref:
            return cur, cur_file
        file_part, _, pointer = ref.partition("#")
        if file_part:
            cur_file = file_part
        target: Any = schemas.get(cur_file)
        if target is None:
            raise ValueError(f"$ref 指向未知文件：{ref}")
        for seg in [x for x in pointer.split("/") if x]:
            target = target.get(seg) if isinstance(target, dict) else None
            if target is None:
                raise ValueError(f"$ref 解析失败：{ref}")
        cur = target
    raise ValueError(f"$ref 解析层数过深（疑似循环引用）：{node!r}")


_TYPE_CHECKS: dict[str, Callable[[Any], bool]] = {
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
    "null": lambda v: v is None,
}


def validate(
    raw_schema: Any,
    value: Any,
    schemas: dict[str, Schema],
    file: str,
    label: str = "",
    path: str = "",
) -> list[str]:
    """校验 value 是否符合 schema。返回错误信息列表（空 = 通过）。"""
    errors: list[str] = []
    _walk(raw_schema, value, file, path, schemas, label, errors)
    return errors


def _walk(
    raw_node: Any,
    val: Any,
    file: str,
    path: str,
    schemas: dict[str, Schema],
    label: str,
    errors: list[str],
) -> None:
    schema, f = deref(raw_node, file, schemas)
    if not isinstance(schema, dict):
        return
    at = f"{label}{path}"

    if "oneOf" in schema:
        ok = any(
            not validate(sub, val, schemas, f, label, path) for sub in schema["oneOf"]
        )
        if not ok:
            errors.append(f"{at}: 值 {json.dumps(val, ensure_ascii=False)} 不满足 oneOf 的任一分支")
        return

    if "const" in schema and val != schema["const"]:
        errors.append(f"{at}: 值 {json.dumps(val, ensure_ascii=False)} ≠ const {json.dumps(schema['const'], ensure_ascii=False)}")

    if "enum" in schema and val not in schema["enum"]:
        errors.append(f"{at}: 值 {json.dumps(val, ensure_ascii=False)} 不在 enum {json.dumps(schema['enum'], ensure_ascii=False)}")

    t = schema.get("type")
    if t is not None:
        check = _TYPE_CHECKS.get(t)
        if check is None:
            raise ValueError(f"schema.py 不认识的 type: {t!r}")
        if not check(val):
            errors.append(f"{at}: 期望 {t}，实际 {type(val).__name__}")
            return  # 类型都不对，继续查子规则只会刷屏

    if isinstance(val, str) and "pattern" in schema and not re.search(schema["pattern"], val):
        errors.append(f'{at}: "{val}" 不匹配 pattern {schema["pattern"]}')

    if isinstance(val, (int, float)) and not isinstance(val, bool):
        if "minimum" in schema and val < schema["minimum"]:
            errors.append(f"{at}: {val} < minimum {schema['minimum']}")
        if "maximum" in schema and val > schema["maximum"]:
            errors.append(f"{at}: {val} > maximum {schema['maximum']}")
        if "exclusiveMinimum" in schema and val <= schema["exclusiveMinimum"]:
            errors.append(f"{at}: {val} ≤ exclusiveMinimum {schema['exclusiveMinimum']}")

    if isinstance(val, list):
        if "minItems" in schema and len(val) < schema["minItems"]:
            errors.append(f"{at}: 数组长度 {len(val)} < minItems {schema['minItems']}")
        if "items" in schema:
            for i, item in enumerate(val):
                _walk(schema["items"], item, f, f"{path}[{i}]", schemas, label, errors)

    if isinstance(val, dict) and (t == "object" or "properties" in schema):
        props: dict[str, Any] = schema.get("properties", {})
        for r in schema.get("required", []):
            if r not in val:
                errors.append(f'{at}: 缺必填字段 "{r}"')

        addl = schema.get("additionalProperties")
        if addl is False:
            for k in val:
                if k not in props:
                    errors.append(f'{at}: 多余字段 "{k}"（additionalProperties: false）')
        elif isinstance(addl, dict):
            for k, v in val.items():
                if k not in props:
                    _walk(addl, v, f, f"{path}.{k}", schemas, label, errors)

        for k, sub in props.items():
            if k in val:
                _walk(sub, val[k], f, f"{path}.{k}", schemas, label, errors)


def mutation_points(
    raw_schema: Any,
    file: str,
    schemas: dict[str, Schema],
    path: str = "",
    acc: dict[str, list[Any]] | None = None,
) -> dict[str, list[Any]]:
    """列出一个 object schema 的「可变异点」，供 gen_contract_tests 生成反例。"""
    if acc is None:
        acc = {"required": [], "enums": [], "closed": []}
    schema, f = deref(raw_schema, file, schemas)
    if not isinstance(schema, dict) or "properties" not in schema:
        return acc

    for r in schema.get("required", []):
        acc["required"].append(f"{path}.{r}")
    if schema.get("additionalProperties") is False:
        acc["closed"].append(path)

    for k, sub in schema["properties"].items():
        s, _ = deref(sub, f, schemas)
        if isinstance(s, dict):
            if "enum" in s:
                acc["enums"].append({"path": f"{path}.{k}", "values": s["enum"]})
            if "properties" in s:
                mutation_points(sub, f, schemas, f"{path}.{k}", acc)
    return acc
