# Harbor Transfer Portal Offline Bundle Protocol v1

Status: normative for schema family `1.x`.

## Purpose

SOURCE and TARGET installations have no network connectivity to one another. Compatibility is defined by this bundle protocol only. A bundle contains no Harbor credentials, tokens or private signing keys.

## Bundle filename

Recommended filename: `DELIVERY-YYYYMMDD-RANDOM.htp.tar.gz`, where `RANDOM` is 6–32 uppercase alphanumeric characters generated from cryptographically secure randomness. `delivery_id` uses the same `DELIVERY-YYYYMMDD-RANDOM` value and is immutable.

## Required top-level members

- `manifest.json`
- `manifest.sig`
- `checksums.sha256`
- one or more payload files/directories referenced by `manifest.json`

All paths use `/` separators and are relative to archive root.

## Signed bytes

`manifest.sig` is an Ed25519 signature over the exact canonical UTF-8 bytes of `manifest.json`. Canonical JSON is: no BOM, UTF-8, sorted object keys, no insignificant whitespace, JSON separators `,` and `:`, and omission of fields whose value is `null`. Implementations must not parse and reserialize using another convention before verification.

## Checksums

`checksums.sha256` contains SHA-256 entries for every payload file. It MUST NOT contain an entry for itself or `manifest.sig`, avoiding circular definitions. `manifest.json` is authenticated by the Ed25519 signature; payload checksum and size are also duplicated in the manifest descriptor.

TARGET verification order is: safe archive structure → supported schema major → canonical manifest/signature → checksum file syntax → payload hashes/sizes → semantic manifest validation → import.

## Manifest

Required fields: `schema_version`, `delivery_id`, UTC `created_at`, `created_by`, non-secret `source` metadata and non-empty `artifacts`.

Container image descriptors use `type=container-image`, repository, tag/reference, source OCI digest, payload path, SHA-256 and size. Helm descriptors use `type=helm-chart`, repository/name, version, optional reliable OCI digest, payload path, SHA-256 and size.

Unknown optional fields within major version 1 SHOULD be ignored when safe. Unsupported major versions MUST be rejected before import.

## Image payload representation

Container images use an OCI image-layout directory (`skopeo copy --all ... oci:<path>:<tag>` style representation), not Docker archive. This preserves multi-architecture index/manifest structure and OCI digests more reliably than a single-image Docker tar representation. The bundle tar provides physical portability; the OCI directory remains an internal payload structure.

## Archive security

Readers MUST reject absolute paths, path traversal, backslashes used as path separators, NUL-containing names, duplicate members, symlinks, hardlinks, device nodes, FIFOs and other unsupported file types. Security-critical members (`manifest.json`, `manifest.sig`, `checksums.sha256`) must occur exactly once. Extraction must occur only into a newly created controlled directory.

## Operation state model

Export legal baseline:
`CREATED → VALIDATING → RUNNING → PACKAGING → VERIFYING → COMPLETED`.
`FAILED` and `CANCELLED` are terminal failure/abort exits from active stages.

Import legal baseline:
`UPLOADED|DISCOVERED → VERIFYING → READY → IMPORTING → VERIFYING_TARGET → COMPLETED`.
`FAILED`, `REJECTED` and `CANCELLED` are terminal states. `REJECTED` means the bundle was refused before import due to trust, compatibility or validation policy.

Illegal transitions are domain errors and must never be silently coerced.

Per-artifact states: `PENDING`, `RUNNING`, `IMPORTED`, `SKIPPED`, `CONFLICT`, `FAILED`, `VERIFIED`. `SKIPPED` is a successful idempotent outcome only when policy confirms the target already contains the expected artifact. `CONFLICT` means the same logical destination exists with different verified content.
