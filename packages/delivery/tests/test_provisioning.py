from __future__ import annotations

import sys
import unittest
from pathlib import Path

DELIVERY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DELIVERY_ROOT))

from codentum_delivery.provisioning import (
    SchemaError,
    connectivity_status,
    load_schema,
    parse_schema,
    validate_submission,
)


class ProvisioningTests(unittest.TestCase):
    def test_example_schema_validates_without_persisting_values(self) -> None:
        schema = load_schema(
            DELIVERY_ROOT / "codentum_delivery" / "provisioning" / "provisioning.example.json"
        )
        valid = validate_submission(
            schema,
            {"provider.api_key": "a" * 24, "service.endpoint": "https://example.invalid/api"},
        )
        self.assertTrue(valid.valid)
        self.assertEqual(valid.issues, ())
        self.assertFalse(hasattr(valid, "values"))

    def test_secret_values_and_defaults_are_forbidden_in_schema_metadata(self) -> None:
        with self.assertRaisesRegex(SchemaError, "unknown metadata"):
            parse_schema(
                {
                    "version": 1,
                    "projectId": "p",
                    "title": "Provisioning",
                    "fields": [
                        {
                            "id": "api.key",
                            "label": "Key",
                            "kind": "secret",
                            "required": True,
                            "value": "must-not-be-here",
                        }
                    ],
                }
            )

    def test_validation_errors_never_echo_the_submitted_secret(self) -> None:
        schema = parse_schema(
            {
                "version": 1,
                "projectId": "p",
                "title": "Provisioning",
                "fields": [
                    {
                        "id": "api.key",
                        "label": "Key",
                        "kind": "secret",
                        "required": True,
                        "validation": {"minLength": 30},
                    }
                ],
            }
        )
        submitted_secret = "sensitive-but-too-short"
        result = validate_submission(schema, {"api.key": submitted_secret})
        self.assertFalse(result.valid)
        self.assertNotIn(submitted_secret, repr(result))

    def test_connectivity_is_never_claimed_without_a_connector(self) -> None:
        status = connectivity_status()
        self.assertEqual(status.status, "not_available")
        self.assertNotIn("success", status.status)


if __name__ == "__main__":
    unittest.main()
