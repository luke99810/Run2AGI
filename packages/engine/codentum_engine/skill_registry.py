"""Runtime Skill projection for user-selected local Skills and cloud catalogs.

The desktop can already send selected resources in ``resourceSelections``. This
module is the engine-side bridge that turns those selections into concrete
``.codentum/skills/shared/<id>/SKILL.md`` entries and returns Skill ids that can
be appended to the current Worker RoleSpec.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from codentum_contracts.state import PacketId, RoleId

_MAX_SKILL_BYTES = 256 * 1024
_MAX_CATALOG_BYTES = 512 * 1024
_SAFE_ID_RE = re.compile(r"[^a-z0-9_.-]+")


class SkillRegistryError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SkillResolution:
    skill_ids: tuple[str, ...]
    projected: tuple[dict[str, object], ...]
    cloud_search: dict[str, object]
    degraded: bool
    degradation_reasons: tuple[str, ...]


def resolve_dynamic_skills(
    *,
    payload: Mapping[str, Any],
    requirement_text: str,
    packet_id: PacketId,
    role: RoleId,
    shared_dir: Path,
    projection_dir: Path,
    cloud_catalog: str | None = None,
    cloud_limit: int = 3,
) -> SkillResolution:
    """Project request-scoped Skills and write the UI-facing projection."""

    shared_dir.mkdir(parents=True, exist_ok=True)
    projection_dir.mkdir(parents=True, exist_ok=True)

    projected: list[dict[str, object]] = []
    reasons: list[str] = []
    for selection in _skill_resource_selections(payload, role):
        try:
            projected.append(_project_local_skill(selection, shared_dir, role=role))
        except SkillRegistryError as exc:
            reasons.append(f"local_skill_rejected:{exc}")

    cloud_report: dict[str, object] = {
        "enabled": bool(cloud_catalog),
        "catalog": cloud_catalog or "",
        "query": _truncate(requirement_text, 500),
        "matchedCount": 0,
        "selected": [],
        "degraded": False,
        "degradationReasons": [],
    }
    if cloud_catalog:
        try:
            cloud_projected = _project_cloud_skills(
                location=cloud_catalog,
                requirement_text=requirement_text,
                role=role,
                shared_dir=shared_dir,
                limit=max(0, cloud_limit),
            )
            projected.extend(cloud_projected)
            cloud_report["matchedCount"] = len(cloud_projected)
            cloud_report["selected"] = [
                {
                    "id": item["id"],
                    "name": item["name"],
                    "sourceId": item["sourceId"],
                    "matchScore": item.get("matchScore", 0),
                }
                for item in cloud_projected
            ]
        except SkillRegistryError as exc:
            reason = f"cloud_skill_catalog_unavailable:{exc}"
            reasons.append(reason)
            cloud_report["degraded"] = True
            cloud_report["degradationReasons"] = [reason]

    seen: set[str] = set()
    skill_ids: list[str] = []
    unique_projected: list[dict[str, object]] = []
    for item in sorted(projected, key=lambda entry: (str(entry["origin"]), str(entry["id"]))):
        skill_id = str(item["id"])
        if skill_id in seen:
            continue
        seen.add(skill_id)
        skill_ids.append(skill_id)
        unique_projected.append(item)

    result = SkillResolution(
        skill_ids=tuple(skill_ids),
        projected=tuple(unique_projected),
        cloud_search=cloud_report,
        degraded=bool(reasons),
        degradation_reasons=tuple(reasons),
    )
    _write_projection(
        projection_dir,
        packet_id=packet_id,
        role=role,
        shared_dir=shared_dir,
        result=result,
    )
    return result


def _skill_resource_selections(
    payload: Mapping[str, Any],
    role: RoleId,
) -> tuple[Mapping[str, Any], ...]:
    contract = payload.get("resourceSelectionContract")
    if contract not in (None, "codentum.resource-selection.v1"):
        return ()
    raw = payload.get("resourceSelections")
    if not isinstance(raw, list):
        return ()

    selected: list[Mapping[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict) or item.get("kind") != "skill":
            continue
        scope = item.get("scope")
        role_id = item.get("roleId")
        if scope == "role" and role_id not in (None, "", str(role)):
            continue
        selected.append(item)
    return tuple(selected)


def _project_local_skill(
    selection: Mapping[str, Any],
    shared_dir: Path,
    *,
    role: RoleId,
) -> dict[str, object]:
    source_kind = str(selection.get("sourceKind", ""))
    if source_kind == "git_url":
        raise SkillRegistryError("git_url Skill 需要先同步到本地目录后再投影")
    if source_kind not in {"file", "folder"}:
        raise SkillRegistryError(f"不支持的本地 Skill 来源: {source_kind or '<missing>'}")

    local_path = selection.get("localPath")
    if not isinstance(local_path, str) or not local_path.strip():
        raise SkillRegistryError("localPath 为空")
    skill_path = _skill_markdown_path(Path(local_path), source_kind=source_kind)
    body = _read_limited_text(skill_path, kind="Skill")
    title, description = _extract_skill_metadata(body)
    name = title or str(selection.get("label") or selection.get("id") or "local-skill")
    digest = hashlib.sha256(f"{skill_path.resolve()}\n{body}".encode()).hexdigest()
    skill_id = _dynamic_skill_id("local", name, digest)
    _write_skill(
        shared_dir / skill_id,
        skill_id=skill_id,
        body=body,
        manifest={
            "id": skill_id,
            "name": name,
            "description": description,
            "origin": "local",
            "role": str(role),
            "sourceKind": source_kind,
            "sourcePath": str(skill_path),
            "sourceResourceId": str(selection.get("id", "")),
            "digest": f"sha256:{hashlib.sha256(body.encode('utf-8')).hexdigest()}",
        },
    )
    return {
        "id": skill_id,
        "name": name,
        "description": description,
        "origin": "local",
        "sourceId": str(selection.get("id", "")),
        "sourcePath": str(skill_path),
        "role": str(role),
    }


def _project_cloud_skills(
    *,
    location: str,
    requirement_text: str,
    role: RoleId,
    shared_dir: Path,
    limit: int,
) -> tuple[dict[str, object], ...]:
    if limit <= 0:
        return ()
    catalog = _load_cloud_catalog(location)
    entries = catalog.get("skills")
    if not isinstance(entries, list):
        raise SkillRegistryError("云 Skill catalog 缺少 skills 数组")

    ranked: list[tuple[int, str, Mapping[str, Any]]] = []
    query_tokens = _tokens(requirement_text)
    for raw in entries:
        if not isinstance(raw, dict):
            continue
        if not _role_matches(raw, role):
            continue
        score = _score_cloud_skill(query_tokens, raw)
        if score <= 0:
            continue
        ranked.append((score, str(raw.get("id", "")), raw))
    ranked.sort(key=lambda item: (-item[0], item[1]))

    projected: list[dict[str, object]] = []
    for score, _entry_id, entry in ranked[:limit]:
        body = _cloud_skill_body(entry)
        if body is None:
            continue
        raw_id = str(entry.get("id") or entry.get("name") or "cloud-skill")
        name = str(entry.get("name") or raw_id)
        description = str(entry.get("description") or "")
        digest = hashlib.sha256(f"{location}\n{raw_id}\n{body}".encode()).hexdigest()
        skill_id = _dynamic_skill_id("cloud", raw_id, digest)
        _write_skill(
            shared_dir / skill_id,
            skill_id=skill_id,
            body=body,
            manifest={
                "id": skill_id,
                "name": name,
                "description": description,
                "origin": "cloud",
                "role": str(role),
                "sourceCatalog": location,
                "sourceSkillId": raw_id,
                "tags": _string_tuple(entry.get("tags")),
                "digest": f"sha256:{hashlib.sha256(body.encode('utf-8')).hexdigest()}",
                "matchScore": score,
            },
        )
        projected.append(
            {
                "id": skill_id,
                "name": name,
                "description": description,
                "origin": "cloud",
                "sourceId": raw_id,
                "sourceCatalog": location,
                "role": str(role),
                "matchScore": score,
            }
        )
    return tuple(projected)


def _load_cloud_catalog(location: str) -> Mapping[str, Any]:
    if location.startswith(("https://", "http://")):
        with urllib.request.urlopen(location, timeout=5.0) as response:  # noqa: S310
            data = response.read(_MAX_CATALOG_BYTES + 1)
        if len(data) > _MAX_CATALOG_BYTES:
            raise SkillRegistryError("云 Skill catalog 超过大小限制")
        text = data.decode("utf-8", errors="replace")
    else:
        text = _read_limited_text(Path(location), kind="云 Skill catalog", limit=_MAX_CATALOG_BYTES)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SkillRegistryError(f"云 Skill catalog 不是有效 JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SkillRegistryError("云 Skill catalog 顶层必须是对象")
    return parsed


def _skill_markdown_path(path: Path, *, source_kind: str) -> Path:
    if source_kind == "folder":
        path = path / "SKILL.md"
    if not path.exists():
        raise SkillRegistryError(f"找不到 SKILL.md: {path}")
    if not path.is_file():
        raise SkillRegistryError(f"Skill 来源不是文件: {path}")
    if path.is_symlink():
        raise SkillRegistryError(f"Skill 来源不能是符号链接: {path}")
    return path


def _read_limited_text(path: Path, *, kind: str, limit: int = _MAX_SKILL_BYTES) -> str:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise SkillRegistryError(f"无法读取 {kind}: {exc}") from exc
    if size > limit:
        raise SkillRegistryError(f"{kind} 超过大小限制")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise SkillRegistryError(f"{kind} 必须是 UTF-8 文本: {exc}") from exc
    except OSError as exc:
        raise SkillRegistryError(f"无法读取 {kind}: {exc}") from exc
    if not text.strip():
        raise SkillRegistryError(f"{kind} 为空")
    return text


def _extract_skill_metadata(body: str) -> tuple[str, str]:
    title = ""
    description = ""
    lines = body.splitlines()
    if lines and lines[0].strip() == "---":
        for line in lines[1:]:
            if line.strip() == "---":
                break
            if line.startswith("name:"):
                title = line.split(":", 1)[1].strip().strip("\"'")
            elif line.startswith("description:"):
                description = line.split(":", 1)[1].strip().strip("\"'")
    if not title:
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#"):
                title = stripped.lstrip("#").strip()
                break
    return title, description


def _cloud_skill_body(entry: Mapping[str, Any]) -> str | None:
    body = entry.get("body") or entry.get("skillBody") or entry.get("prompt")
    if not isinstance(body, str) or not body.strip():
        return None
    if len(body.encode("utf-8")) > _MAX_SKILL_BYTES:
        return None
    return body


def _role_matches(entry: Mapping[str, Any], role: RoleId) -> bool:
    roles = _string_tuple(entry.get("roles"))
    return not roles or str(role) in roles or "*" in roles


def _score_cloud_skill(query_tokens: set[str], entry: Mapping[str, Any]) -> int:
    haystack = " ".join(
        (
            str(entry.get("id", "")),
            str(entry.get("name", "")),
            str(entry.get("description", "")),
            " ".join(_string_tuple(entry.get("tags"))),
            " ".join(_string_tuple(entry.get("roles"))),
        )
    )
    hay_tokens = _tokens(haystack)
    overlap = query_tokens & hay_tokens
    return len(overlap)


def _tokens(text: str) -> set[str]:
    lowered = text.lower()
    tokens = set(re.findall(r"[a-z0-9_]{2,}", lowered))
    cjk_chunks = re.findall(r"[\u4e00-\u9fff]{2,}", text)
    for chunk in cjk_chunks:
        for size in range(2, min(6, len(chunk)) + 1):
            tokens.update(chunk[index : index + size] for index in range(0, len(chunk) - size + 1))
    return tokens


def _string_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list | tuple):
        return tuple(str(item) for item in value if isinstance(item, str) and item)
    return ()


def _dynamic_skill_id(prefix: Literal["local", "cloud"], name: str, digest: str) -> str:
    slug = _slug(name)
    return f"{prefix}-{slug}-{digest[:12]}"


def _slug(value: str) -> str:
    slug = _SAFE_ID_RE.sub("-", value.lower()).strip("-._")
    return (slug or "skill")[:40].strip("-._") or "skill"


def _write_skill(path: Path, *, skill_id: str, body: str, manifest: Mapping[str, object]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    full_manifest = {
        "schemaVersion": 1,
        "id": skill_id,
        "version": "0.0.0",
        "projectedAt": _now_iso(),
        **dict(manifest),
    }
    (path / "manifest.json").write_text(
        json.dumps(full_manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    (path / "SKILL.md").write_text(body.rstrip() + "\n", encoding="utf-8")


def _write_projection(
    projection_dir: Path,
    *,
    packet_id: PacketId,
    role: RoleId,
    shared_dir: Path,
    result: SkillResolution,
) -> None:
    payload = {
        "schemaVersion": 1,
        "updatedAt": _now_iso(),
        "packetId": str(packet_id),
        "role": str(role),
        "sharedDir": str(shared_dir),
        "projectedCount": len(result.projected),
        "projected": list(result.projected),
        "cloudSearch": result.cloud_search,
        "degraded": result.degraded,
        "degradationReasons": list(result.degradation_reasons),
    }
    (projection_dir / "projection.json").write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
