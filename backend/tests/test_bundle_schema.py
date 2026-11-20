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


@pytest.mark.parametrize(
    "version,source,accepted",
    [
        ("1.0", {"contour": "SOURCE", "harbor": "harbor.source.local"}, True),
        ("1.0", {"contour": "SOURCE", "registry_type": "nexus",
                 "registry": "https://docker-nexus.internal"}, False),
        ("1.0", {"contour": "SOURCE", "harbor": "harbor.source.local",
                 "registry_type": "harbor"}, False),
        ("1.1", {"contour": "SOURCE", "registry_type": "nexus",
                 "registry": "https://docker-nexus.internal"}, True),
        ("1.1", {"contour": "SOURCE", "harbor": "harbor.source.local"}, False),
        ("1.1", {"contour": "SOURCE", "registry_type": "harbor",
                 "harbor": "harbor.source.local"}, False),
    ],
)
def test_manifest_schema_version_binds_source_provenance(
    version: str, source: dict[str, str], accepted: bool,
) -> None:
    payload = valid_manifest()
    payload["schema_version"] = version
    payload["source"] = source
    validator = jsonschema.Draft202012Validator(load_schema("manifest-v1.schema.json"))
    if accepted:
        validator.validate(payload)
    else:
        with pytest.raises(jsonschema.ValidationError):
            validator.validate(payload)
