"""RoleSpec 加载器.

RoleSpec 是角色的真身; 本模块只负责把磁盘上的 spec 变成经过校验的
`RoleSpec` 对象. 提示词、工具面、状态转换都只能从这里加载出的 spec 派生.
"""

from __future__ import annotations

import json
from pathlib import Path

from codentum_contracts.state import RoleId, RoleSpec
from pydantic import ValidationError

__all__ = [
    "RoleSpecLoadError",
    "default_specs_dir",
    "load_builtin_role_specs",
    "load_role_spec_file",
    "load_role_specs_dir",
]


class RoleSpecLoadError(ValueError):
    """RoleSpec 无法加载或违反 schema 外约束。"""


def default_specs_dir() -> Path:
    """仓库内置 RoleSpec 目录。"""
    return Path(__file__).resolve().parents[1] / "specs"


def load_role_spec_file(path: Path | str) -> RoleSpec:
    """从 JSON 文件加载单个 RoleSpec.

    先只支持 JSON, 避免为了第一个可运行切片引入 YAML 运行时依赖. 后续若团队
    决定 RoleSpec 必须写成 YAML, 应在这里集中扩展, 而不是让调用方各自解析.
    """
    spec_path = Path(path)
    if spec_path.suffix != ".json":
        raise RoleSpecLoadError(f"暂只支持 .json RoleSpec: {spec_path}")

    try:
        raw = json.loads(spec_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RoleSpecLoadError(f"无法读取 RoleSpec: {spec_path}") from exc
    except json.JSONDecodeError as exc:
        raise RoleSpecLoadError(f"RoleSpec 不是合法 JSON: {spec_path}") from exc

    try:
        spec = RoleSpec.model_validate(raw)
    except ValidationError as exc:
        raise RoleSpecLoadError(f"RoleSpec schema 校验失败: {spec_path}") from exc

    _validate_schema_external_constraints(spec)
    return spec


def load_role_specs_dir(path: Path | str) -> tuple[RoleSpec, ...]:
    """按文件名稳定加载目录下所有 JSON RoleSpec。"""
    specs_dir = Path(path)
    specs = tuple(load_role_spec_file(p) for p in sorted(specs_dir.glob("*.json")))
    _reject_duplicate_roles(specs)
    return specs


def load_builtin_role_specs() -> tuple[RoleSpec, ...]:
    """加载仓库内置 RoleSpec。"""
    return load_role_specs_dir(default_specs_dir())


def _validate_schema_external_constraints(spec: RoleSpec) -> None:
    """强制 JSON Schema 表达不了的条件约束。"""
    if spec.id == "guardian" and spec.usesModel:
        raise RoleSpecLoadError("guardian.usesModel 必须为 false; 确定性拦截器不能调用模型.")


def _reject_duplicate_roles(specs: tuple[RoleSpec, ...]) -> None:
    seen: set[RoleId] = set()
    for spec in specs:
        if spec.id in seen:
            raise RoleSpecLoadError(f"RoleSpec 重复定义: {spec.id}")
        seen.add(spec.id)
