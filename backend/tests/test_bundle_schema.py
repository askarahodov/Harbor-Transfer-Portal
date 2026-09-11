import json
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "docs" / "schema"


def load_schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def valid_manifest() -> dict:
    return {
        "schema_version": "1.0",
        "delivery_id": "DELIVERY-20260911-ABC123",
        "created_at": "2026-09-11T06:00:00Z",
        "created_by": "operator",
        "source": {"contour": "SOURCE", "harbor": "harbor.source.local"},
        "artifacts": [
            {
                "type": "container-image",
                "repository": "project/app",
                "reference": "1.0.0",
                "source_digest": "sha256:" + "a" * 64,
                "payload_path": "payload/images/app/index.json",
                "payload_sha256": "b" * 64,
                "payload_size": 123,
            }
        ],
    }


def test_manifest_schema_accepts_v1_fixture() -> None:
    jsonschema.Draft202012Validator(load_schema("manifest-v1.schema.json")).validate(valid_manifest())


def test_manifest_schema_rejects_unsupported_major() -> None:
    payload = valid_manifest()
    payload["schema_version"] = "2.0"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(load_schema("manifest-v1.schema.json")).validate(payload)


def test_receipt_schema_accepts_terminal_receipt() -> None:
    receipt = {
        "schema_version": "1.0",
        "delivery_id": "DELIVERY-20260911-ABC123",
        "operation_id": "op-123",
        "status": "COMPLETED",
        "completed_at": "2026-09-11T06:30:00Z",
        "artifacts": [{"artifact_index": 0, "status": "VERIFIED"}],
    }
    jsonschema.Draft202012Validator(load_schema("receipt-v1.schema.json")).validate(receipt)
