# Changelog

All notable user-facing changes to Harbor Transfer Portal are documented here.
The project follows Semantic Versioning for product releases.

## Unreleased — Nexus Docker v2 (release candidate; NOT in main)

This section records **draft PR changes**, not published product features.
Release version, merge, offline kit and regional deployment have **not** been approved.
Detailed evidence and acceptance tracker: [Nexus release readiness](docs/nexus-release-readiness-2026-11-03.md).

### Proposed improvements (draft; not shipped)
- Nexus Docker Registry v2 connection probe, independent of any particular image (#438).
- Type-aware Nexus SOURCE exact repository/tag inspection and selection, without Harbor browse API calls (#439).
- Nexus SOURCE export preview/digest revalidation; fail-closed legacy Helm rejection (#440).
- Signed Nexus origin in Bundle v1.1, maintaining historical Harbor v1.0 canonical metadata (#441).
- Full disposable Nexus 3.69.0-02 OSS SOURCE API → signed bundle → download test (#442).
- Nexus TARGET Docker v2 destination planning and disposable SOURCE→TARGET API E2E: separate hosted connectors, signed handoff, digest/receipt, SAME and conflict default-deny/explicit skip (#446, draft; not shipped).

### Verified / not yet verified
- Integrated draft branch #442: [CI success](https://github.com/askarahodov/Harbor-Transfer-Portal/actions/runs/41633770476), [Nexus Docker v2 E2E success](https://github.com/askarahodov/Harbor-Transfer-Portal/actions/runs/41633770502).
- CI verified for disposable Nexus TARGET import E2E (#446): [Nexus E2E](https://github.com/askarahodov/Harbor-Transfer-Portal/actions/runs/41649019315). Not verified: real corporate TARGET/network acceptance (#436), physical isolation and mixed registry combinations (#434), remaining UX (#431), Helm decision (#435).
- Independent Harbor export incident #422 remains open.
- **Release gate: NO-GO** pending the items above and review/merges.

## 1.0.0 - 2026-09-14

### Added
- SOURCE and TARGET portal modes for physically isolated Harbor contours.
- Signed Offline Bundle Protocol v1 with Ed25519 signatures and payload checksums.
- Container image export/import through Skopeo with digest verification.
- Helm OCI chart export/import with signed package integrity, safe cross-registry replay semantics and default-deny conflict handling.
- Role-based access for admin, operator and viewer roles.
- Persistent operation history, immutable import receipts and CSV/PDF reporting.
- Offline installation kit with prebuilt images, checksums and one-command install for SOURCE or TARGET.
- Backup, restore, upgrade and uninstall lifecycle helpers.
- Scoped CI gates covering backend, frontend, protocol/security, Compose, OCI integration, clean-host installation and isolated SOURCE-to-TARGET acceptance.

### Security
- Archive extraction, signing-key handling, registry credentials and transfer paths are validated fail-closed.
- Harbor credentials, JWT secrets and SOURCE private signing keys are excluded from transfer and release archives.
- Conflicting TARGET tags or chart versions are not overwritten without an explicit authorized policy.

### Known limitations
- SOURCE and TARGET remain intentionally disconnected; physical media handling is an operator responsibility.
- Rollback across incompatible database migrations may require restoring a pre-upgrade backup.
- Helm OCI manifest digests may differ after a cross-registry push; signed chart package integrity and verified SOURCE-to-TARGET provenance are used for replay semantics.
