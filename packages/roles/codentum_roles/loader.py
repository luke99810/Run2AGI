"""RoleSpec 加载器.

RoleSpec 是角色的真身; 本模块只负责把磁盘上的 spec 变成经过校验的
`RoleSpec` 对象. 提示词、工具面、状态转换都只能从这里加载出的 spec 派生.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from codentum_contracts.state import RoleId, RoleSpec
from pydantic import ValidationError

__all__ = [
    "RoleMcpLoadError",
    "RolePromptLoadError",
    "RoleSkillLoadError",
    "RoleSpecLoadError",
    "default_mcp_dir",
    "default_prompts_dir",
    "default_skills_dir",
    "default_specs_dir",
    "load_builtin_mcp_services",
    "load_builtin_role_specs",
    "load_role_prompt",
    "load_role_skill_prompt",
    "load_role_spec_file",
    "load_role_specs_dir",
    "project_mcp_services",
    "project_role_skills",
]


class RoleSpecLoadError(ValueError):
    """RoleSpec 无法加载或违反 schema 外约束。"""


class RolePromptLoadError(ValueError):
    """RoleSpec.promptRef 无法解析或读取。"""


class RoleSkillLoadError(ValueError):
    """RoleSpec.skills 无法解析或读取。"""


class RoleMcpLoadError(ValueError):
    """MCP 服务投影无法解析或读取。"""


def default_specs_dir() -> Path:
    """仓库内置 RoleSpec 目录。"""
    return Path(__file__).resolve().parents[1] / "specs"


def default_prompts_dir() -> Path:
    """仓库内置角色 prompt 目录。"""
    return Path(__file__).resolve().parents[1] / "prompts"


def default_skills_dir() -> Path:
    """仓库内置 Skill 目录。"""
    return Path(__file__).resolve().parents[1] / "skills"


def default_mcp_dir() -> Path:
    """仓库内置 MCP 服务投影目录。"""
    return Path(__file__).resolve().parents[1] / "mcp"


def load_builtin_mcp_services(mcp_dir: Path | str | None = None) -> tuple[dict[str, object], ...]:
    """加载内置 MCP 服务投影。

    这里的 MCP 清单是运行时展示与权限收敛的输入，不等于已经启动外部 MCP
    server。每个服务必须显式给出连接状态和错误原因，避免 UI 把路线图说成
    已接入能力。
    """

    root = Path(mcp_dir or default_mcp_dir())
    services = tuple(_load_mcp_service_file(path) for path in sorted(root.glob("*.json")))
    _reject_duplicate_mcp_services(services)
    return services


def project_mcp_services(
    target_dir: Path | str,
    *,
    mcp_dir: Path | str | None = None,
) -> tuple[Path, ...]:
    """把内置 MCP 服务清单投影到项目状态目录。

    `packages/roles/mcp/*.json` 是 B 侧真源；`.codentum/mcp/*.json` 是 C
    和运行时读取的项目副本。投影只搬运通过校验的服务，不做前端推断。
    """

    target_root = Path(target_dir)
    written: list[Path] = []
    for service in load_builtin_mcp_services(mcp_dir):
        service_id = str(service["id"])
        target_root.mkdir(parents=True, exist_ok=True)
        target_path = target_root / f"{service_id}.json"
        try:
            target_path.write_text(
                json.dumps(service, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            raise RoleMcpLoadError(f"无法投影 MCP 服务[{service_id}]: {target_path}") from exc
        written.append(target_path)
    return tuple(written)


def load_role_prompt(spec: RoleSpec, prompts_dir: Path | str | None = None) -> str | None:
    """读取 RoleSpec.promptRef 指向的角色提示词。

    prompt 是软性方向, 不是硬约束; 没有 promptRef 的临时测试 RoleSpec 仍允许加载。
    但一旦声明了 promptRef, 就必须能解析到 prompts/ 下的普通文件, 避免路径穿越
    或 dangling prompt 静默进入模型输入。
    """
    if spec.promptRef is None:
        return None

    prompt_path = _resolve_prompt_ref(spec.promptRef, prompts_dir or default_prompts_dir())
    try:
        return prompt_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RolePromptLoadError(f"无法读取 RoleSpec[{spec.id}] prompt: {prompt_path}") from exc


def load_role_skill_prompt(skill_id: str, skills_dir: Path | str | None = None) -> str:
    """读取 RoleSpec.skills 指向的 Skill 正文。

    Skill 进入 PromptBundle 时读取的是 SKILL.md, 不是 manifest.json。manifest 用于登记
    元数据; SKILL.md 才是 Worker 能实际执行的能力说明。因此引用存在但正文缺失时必须
    fail-closed, 避免 UI 显示“已接入”而运行时没有任何 Skill 指令。
    """
    skill_dir = _resolve_skill_dir(skill_id, skills_dir or default_skills_dir())
    skill_prompt_path = skill_dir / "SKILL.md"
    try:
        return skill_prompt_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RoleSkillLoadError(f"无法读取 Skill[{skill_id}] 正文: {skill_prompt_path}") from exc


def project_role_skills(
    skill_ids: Iterable[str],
    target_dir: Path | str,
    *,
    skills_dir: Path | str | None = None,
) -> tuple[Path, ...]:
    """把内置 Skill 稳定投影到项目共享 Skill 目录。

    `packages/roles/skills/` 是 Git 内的真源; `.codentum/skills/shared/`
    是运行时共享副本。投影前先走同一套 manifest / SKILL.md 校验, 避免把
    一个 UI 可见但 Worker 不可用的 Skill 放进项目状态。
    """

    source_root = Path(skills_dir or default_skills_dir())
    target_root = Path(target_dir)
    written: list[Path] = []
    for skill_id in sorted(set(skill_ids)):
        source_skill_dir = _resolve_skill_dir(skill_id, source_root)
        _load_skill_manifest(source_root, skill_id)
        load_role_skill_prompt(skill_id, source_root)

        target_skill_dir = target_root / skill_id
        target_skill_dir.mkdir(parents=True, exist_ok=True)
        for filename in ("manifest.json", "SKILL.md"):
            source_path = source_skill_dir / filename
            target_path = target_skill_dir / filename
            try:
                target_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
            except OSError as exc:
                raise RoleSkillLoadError(
                    f"无法投影 Skill[{skill_id}] 到共享空间: {target_path}"
                ) from exc
            written.append(target_path)
    return tuple(written)


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
    _validate_skill_refs(specs, default_skills_dir())
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


def _reject_duplicate_mcp_services(services: tuple[dict[str, object], ...]) -> None:
    seen: set[str] = set()
    for service in services:
        service_id = str(service["id"])
        if service_id in seen:
            raise RoleMcpLoadError(f"MCP 服务重复定义: {service_id}")
        seen.add(service_id)


def _validate_skill_refs(specs: tuple[RoleSpec, ...], skills_dir: Path) -> None:
    for spec in specs:
        if spec.skills is None:
            continue
        seen: set[str] = set()
        for skill in spec.skills:
            if skill.id in seen:
                raise RoleSkillLoadError(f"RoleSpec[{spec.id}] 重复声明 Skill: {skill.id}")
            seen.add(skill.id)
            _load_skill_manifest(skills_dir, skill.id)
            load_role_skill_prompt(skill.id, skills_dir)


def _load_skill_manifest(skills_dir: Path, skill_id: str) -> dict[str, object]:
    path = _resolve_skill_dir(skill_id, skills_dir) / "manifest.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RoleSkillLoadError(f"RoleSpec 引用的 Skill 不存在: {skill_id}") from exc
    except json.JSONDecodeError as exc:
        raise RoleSkillLoadError(f"Skill manifest 不是合法 JSON: {path}") from exc
    if not isinstance(raw, dict):
        raise RoleSkillLoadError(f"Skill manifest 必须是对象: {path}")
    if raw.get("id") != skill_id:
        raise RoleSkillLoadError(f"Skill manifest id 与目录名不一致: {path}")
    return raw


def _load_mcp_service_file(path: Path) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RoleMcpLoadError(f"无法读取 MCP 服务清单: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RoleMcpLoadError(f"MCP 服务清单不是合法 JSON: {path}") from exc
    if not isinstance(raw, dict):
        raise RoleMcpLoadError(f"MCP 服务清单必须是对象: {path}")
    _validate_mcp_service(raw, path)
    return raw


def _validate_mcp_service(raw: dict[str, object], path: Path) -> None:
    service_id = raw.get("id")
    if not isinstance(service_id, str) or not service_id:
        raise RoleMcpLoadError(f"MCP 服务 id 不能为空: {path}")
    if Path(service_id).parts != (service_id,) or service_id in {".", ".."}:
        raise RoleMcpLoadError(f"MCP 服务 id 不允许路径片段: {path}")
    if raw.get("schemaVersion") != 1:
        raise RoleMcpLoadError(f"MCP 服务 schemaVersion 必须为 1: {path}")
    if raw.get("transport") not in {"stdio", "http", "sse"}:
        raise RoleMcpLoadError(f"MCP 服务 transport 非法: {path}")
    if raw.get("status") not in {"connected", "connecting", "disconnected", "error"}:
        raise RoleMcpLoadError(f"MCP 服务 status 非法: {path}")
    if raw.get("authentication") not in {"not_required", "configured", "missing", "unknown"}:
        raise RoleMcpLoadError(f"MCP 服务 authentication 非法: {path}")
    if not isinstance(raw.get("name"), str) or not raw["name"]:
        raise RoleMcpLoadError(f"MCP 服务 name 不能为空: {path}")
    tools = raw.get("tools")
    if not isinstance(tools, list) or not all(isinstance(tool, str) and tool for tool in tools):
        raise RoleMcpLoadError(f"MCP 服务 tools 必须是非空字符串数组: {path}")
    if "error" in raw and not isinstance(raw["error"], str):
        raise RoleMcpLoadError(f"MCP 服务 error 必须是字符串: {path}")
    if "configSource" in raw and not isinstance(raw["configSource"], str):
        raise RoleMcpLoadError(f"MCP 服务 configSource 必须是字符串: {path}")


def _resolve_skill_dir(skill_id: str, skills_dir: Path | str) -> Path:
    ref = Path(skill_id)
    if ref.is_absolute():
        raise RoleSkillLoadError(f"Skill id 不允许使用绝对路径: {skill_id!r}")
    if not skill_id or len(ref.parts) != 1 or any(part in {"", ".", ".."} for part in ref.parts):
        raise RoleSkillLoadError(f"Skill id 不允许路径穿越或空路径片段: {skill_id!r}")

    root = Path(skills_dir)
    path = root / skill_id
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise RoleSkillLoadError(f"Skill 必须位于 skills/ 下: {skill_id!r}") from exc
    if not path.is_dir():
        raise RoleSkillLoadError(f"RoleSpec 引用的 Skill 不存在: {skill_id}")
    return path


def _resolve_prompt_ref(prompt_ref: str, prompts_dir: Path | str) -> Path:
    ref = Path(prompt_ref)
    if ref.is_absolute():
        raise RolePromptLoadError(f"promptRef 不允许使用绝对路径: {prompt_ref!r}")
    if any(part in {"", ".", ".."} for part in ref.parts):
        raise RolePromptLoadError(f"promptRef 不允许路径穿越或空路径片段: {prompt_ref!r}")
    if ref.suffix != ".md":
        raise RolePromptLoadError(f"promptRef 暂只支持 .md 文件: {prompt_ref!r}")

    root = Path(prompts_dir)
    path = root / ref
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise RolePromptLoadError(f"promptRef 必须位于 prompts/ 下: {prompt_ref!r}") from exc
    if not path.is_file():
        raise RolePromptLoadError(f"promptRef 指向的文件不存在: {prompt_ref!r}")
    return path
