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
transfer destination. Nexus origins must have an HTTP(S) scheme, host, and
optional port, but **no username/password, path, query or fragment**.
HTTPS is required for deployed regional connectors; plain HTTP remains
accepted only for isolated disposable integration fixtures. `source.harbor` is deliberately absent for Nexus.
The JSON Schema rejects Nexus provenance with `source.harbor` or without
`source.registry`; the Pydantic model enforces the same constraints.
The bundle signature covers all present origin fields and the 1.1 version.

## Version-to-origin contract (validated before signature acceptance)

The 1.0 manifest is the **legacy Harbor format**: it must contain
`source.harbor` and must not include `source.registry_type` or
`source.registry`. The 1.1 manifest is the **explicit Nexus format**:
it must contain `source.registry_type: "nexus"` and `source.registry`,
and must omit `source.harbor`. The JSON Schema (runtime and published
copies) and Pydantic validator enforce this mapping, including when a
manifest has been re-signed with a trusted key. This prevents a 1.0
Nexus-origin manifest being silently interpreted as legacy Harbor by an
old TARGET. There is no automatic 1.0 downgrade for Nexus.

The signed canonical JSON uses sorted keys, compact separators, UTF-8
and excludes absent/null optional fields; old Harbor 1.0 manifest bytes
remain unchanged. TARGET must validate the manifest against the schema
and verify its signature before acting on any source provenance, and
must **never** fetch from the signed `source.registry`. The schema's
major-version acceptance alone does not imply that a particular minor
version can be downgraded to 1.0.

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
- 1.0 Nexus/registry extensions and 1.1 Harbor/absent Nexus markers rejected by JSON Schema and Pydantic
- Nexus origins containing credentials, path/query/fragment or malformed IPv6/port rejected

Release status: **draft pending CI, integration E2E and review**. This
protocol addition does not by itself establish regional Nexus readiness.
