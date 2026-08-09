"""Non-sensitive provisioning schema and in-memory validation.

Credential persistence and connectivity checks are intentionally not implemented here:
they require an OS credential-vault adapter and an A/B-owned connector capability.
"""

from .schema import (
    ConnectivityStatus,
    FieldIssue,
    ProvisioningField,
    ProvisioningSchema,
    SchemaError,
    ValidationResult,
    connectivity_status,
    load_schema,
    parse_schema,
    validate_submission,
)

__all__ = [
    "ConnectivityStatus",
    "FieldIssue",
    "ProvisioningField",
    "ProvisioningSchema",
    "SchemaError",
    "ValidationResult",
    "connectivity_status",
    "load_schema",
    "parse_schema",
    "validate_submission",
]
