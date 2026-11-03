# Offline Bundle v1: Nexus SOURCE provenance (protocol 1.1)

## Decision

Harbor SOURCE keeps **schema_version 1.0** and the existing signed
`source.harbor` field. Canonical JSON bytes are unchanged for legacy
manifests (absent optional fields are excluded from canonical serialization).

Nexus Docker v2 SOURCE produces **schema_version 1.1** with:

```json
{
  "source": {
    "contour": "SOURCE",
    "registry_type": "nexus",
    "registry": "https://docker-nexus.example.internal",
    "portal_version": "0.1.0"
  }
}
```

The signed `source.registry` is an origin URL, not credentials and not a
transfer destination. `source.harbor` is deliberately absent for Nexus.
The JSON Schema rejects Nexus provenance with `source.harbor` or without
`source.registry`; the Pydantic model enforces the same constraints.
The bundle signature covers all present origin fields and the 1.1 version.

## Compatibility and rollout order

1. Update **TARGET first** to a build that validates both Harbor 1.0 and
   Nexus 1.1 manifests. Verify known historical Harbor bundles on TARGET.
2. Update SOURCE. Harbor continues producing 1.0; Nexus may produce 1.1.
3. Run signed bundle creation, handoff, TARGET verification, mapping and
   import on disposable Nexus 3.69.0-02 OSS before releasing.
4. Do not send 1.1 bundles to an old TARGET. Old TARGET versions requiring
   `source.harbor` may reject 1.1; this is intentional, not a reason to
   impersonate Harbor metadata. A rejected bundle must not be imported.

No passwords, bearer tokens, CA material or username are included in
the signed source provenance. `source.registry` is not an authorization
endpoint and must never be used as a network fetch target during offline
TARGET verification.

## Negative and regression tests

- Harbor 1.0 round-trip preserves canonical JSON without new null fields
- Nexus 1.1 signed round-trip and independent verification
- Tampering with Nexus origin invalidates signature
- Missing registry and inconsistent `source.harbor` are rejected

Release status: **draft pending CI, integration E2E and review**. This
protocol addition does not by itself establish regional Nexus readiness.
