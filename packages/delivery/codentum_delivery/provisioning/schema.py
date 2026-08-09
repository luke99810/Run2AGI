"""Strict provisioning metadata parser that never stores or logs submitted values."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

MAX_SCHEMA_BYTES = 1024 * 1024
FIELD_KINDS = frozenset({"text", "secret", "email", "url", "file"})
TOP_LEVEL_KEYS = frozenset({"version", "projectId", "title", "fields"})
FIELD_KEYS = frozenset({"id", "label", "kind", "required", "description", "validation"})
VALIDATION_KEYS = frozenset({"minLength", "maxLength", "pattern", "allowedSchemes", "allowedExtensions"})
FIELD_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class SchemaError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ProvisioningField:
    field_id: str
    label: str
    kind: str
    required: bool
    description: str | None
    min_length: int | None
    max_length: int | None
    pattern: str | None
    allowed_schemes: tuple[str, ...]
    allowed_extensions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProvisioningSchema:
    version: int
    project_id: str
    title: str
    fields: tuple[ProvisioningField, ...]


@dataclass(frozen=True, slots=True)
class FieldIssue:
    field_id: str
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ValidationResult:
    valid: bool
    issues: tuple[FieldIssue, ...]


@dataclass(frozen=True, slots=True)
class ConnectivityStatus:
    status: str
    reason: str


def load_schema(path: Path) -> ProvisioningSchema:
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise SchemaError("provisioning schema path must be a file")
    if resolved.stat().st_size > MAX_SCHEMA_BYTES:
        raise SchemaError("provisioning schema exceeds the 1 MiB limit")
    try:
        decoded: object = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SchemaError("provisioning schema is not valid UTF-8 JSON") from exc
    return parse_schema(decoded)


def parse_schema(value: object) -> ProvisioningSchema:
    if not isinstance(value, dict):
        raise SchemaError("schema must be an object")
    unknown = set(value) - TOP_LEVEL_KEYS
    if unknown:
        raise SchemaError(f"schema contains unknown fields: {', '.join(sorted(unknown))}")
    if value.get("version") != 1:
        raise SchemaError("schema version must be 1")
    project_id = value.get("projectId")
    title = value.get("title")
    raw_fields = value.get("fields")
    if not isinstance(project_id, str) or not project_id.strip():
        raise SchemaError("projectId must be a non-empty string")
    if not isinstance(title, str) or not title.strip():
        raise SchemaError("title must be a non-empty string")
    if not isinstance(raw_fields, list):
        raise SchemaError("fields must be an array")
    fields = tuple(_parse_field(item) for item in raw_fields)
    ids = [field.field_id for field in fields]
    if len(ids) != len(set(ids)):
        raise SchemaError("field ids must be unique")
    return ProvisioningSchema(version=1, project_id=project_id, title=title, fields=fields)


def _parse_field(value: object) -> ProvisioningField:
    if not isinstance(value, dict):
        raise SchemaError("each field must be an object")
    unknown = set(value) - FIELD_KEYS
    if unknown:
        # This rejects value/default keys so credentials cannot leak into schema files.
        raise SchemaError(f"field contains unknown metadata: {', '.join(sorted(unknown))}")
    field_id = value.get("id")
    label = value.get("label")
    kind = value.get("kind")
    required = value.get("required")
    description = value.get("description")
    validation = value.get("validation", {})
    if not isinstance(field_id, str) or FIELD_ID.fullmatch(field_id) is None:
        raise SchemaError("field id must match ^[a-z][a-z0-9_.-]{0,63}$")
    if not isinstance(label, str) or not label.strip():
        raise SchemaError(f"field {field_id}: label must be a non-empty string")
    if not isinstance(kind, str) or kind not in FIELD_KINDS:
        raise SchemaError(f"field {field_id}: unsupported kind")
    if not isinstance(required, bool):
        raise SchemaError(f"field {field_id}: required must be boolean")
    if description is not None and not isinstance(description, str):
        raise SchemaError(f"field {field_id}: description must be a string")
    if not isinstance(validation, dict):
        raise SchemaError(f"field {field_id}: validation must be an object")
    validation_unknown = set(validation) - VALIDATION_KEYS
    if validation_unknown:
        raise SchemaError(
            f"field {field_id}: unknown validation keys: {', '.join(sorted(validation_unknown))}"
        )
    min_length = _optional_non_negative_int(validation.get("minLength"), field_id, "minLength")
    max_length = _optional_non_negative_int(validation.get("maxLength"), field_id, "maxLength")
    if min_length is not None and max_length is not None and min_length > max_length:
        raise SchemaError(f"field {field_id}: minLength exceeds maxLength")
    pattern = validation.get("pattern")
    if pattern is not None:
        if not isinstance(pattern, str) or len(pattern) > 256:
            raise SchemaError(f"field {field_id}: pattern must be a string of at most 256 characters")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise SchemaError(f"field {field_id}: pattern is invalid") from exc
    allowed_schemes = _string_tuple(validation.get("allowedSchemes", []), field_id, "allowedSchemes")
    allowed_extensions = tuple(
        extension.lower() if extension.startswith(".") else f".{extension.lower()}"
        for extension in _string_tuple(validation.get("allowedExtensions", []), field_id, "allowedExtensions")
    )
    return ProvisioningField(
        field_id=field_id,
        label=label,
        kind=kind,
        required=required,
        description=description,
        min_length=min_length,
        max_length=max_length,
        pattern=pattern,
        allowed_schemes=allowed_schemes,
        allowed_extensions=allowed_extensions,
    )


def _optional_non_negative_int(value: object, field_id: str, name: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise SchemaError(f"field {field_id}: {name} must be a non-negative integer")
    return value


def _string_tuple(value: object, field_id: str, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise SchemaError(f"field {field_id}: {name} must be an array of non-empty strings")
    return tuple(value)


def validate_submission(schema: ProvisioningSchema, submission: object) -> ValidationResult:
    """Validate in memory. Values are never copied into the result or written to disk."""

    if not isinstance(submission, dict):
        issue = FieldIssue("$", "invalid_submission", "submission must be an object")
        return ValidationResult(False, (issue,))
    issues: list[FieldIssue] = []
    fields_by_id = {field.field_id: field for field in schema.fields}
    for key in submission:
        if not isinstance(key, str) or key not in fields_by_id:
            issues.append(FieldIssue(str(key), "unknown_field", "field is not declared by the schema"))
    for field in schema.fields:
        raw = submission.get(field.field_id)
        if raw is None or raw == "":
            if field.required:
                issues.append(FieldIssue(field.field_id, "required", "a value is required"))
            continue
        if not isinstance(raw, str):
            issues.append(FieldIssue(field.field_id, "invalid_type", "value must be a string"))
            continue
        _validate_value(field, raw, issues)
    return ValidationResult(not issues, tuple(issues))


def _validate_value(field: ProvisioningField, value: str, issues: list[FieldIssue]) -> None:
    if field.min_length is not None and len(value) < field.min_length:
        issues.append(FieldIssue(field.field_id, "too_short", "value is shorter than allowed"))
    if field.max_length is not None and len(value) > field.max_length:
        issues.append(FieldIssue(field.field_id, "too_long", "value is longer than allowed"))
    if field.pattern is not None and re.fullmatch(field.pattern, value) is None:
        issues.append(FieldIssue(field.field_id, "pattern", "value has an invalid format"))
    if field.kind == "email" and EMAIL.fullmatch(value) is None:
        issues.append(FieldIssue(field.field_id, "email", "value is not a valid email address"))
    if field.kind == "url":
        parsed = urlparse(value)
        schemes = field.allowed_schemes or ("https",)
        if parsed.scheme.lower() not in schemes or not parsed.netloc:
            issues.append(FieldIssue(field.field_id, "url", "value is not an allowed absolute URL"))
    if field.kind == "file" and field.allowed_extensions:
        if Path(value).suffix.lower() not in field.allowed_extensions:
            issues.append(FieldIssue(field.field_id, "extension", "file extension is not allowed"))


def connectivity_status() -> ConnectivityStatus:
    """Expose honest UI state until an A/B connector implements a real probe."""

    return ConnectivityStatus(
        status="not_available",
        reason="No connector capability is attached; connectivity has not been tested",
    )
