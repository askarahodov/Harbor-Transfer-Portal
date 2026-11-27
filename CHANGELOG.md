# Changelog

All notable user-facing changes to Harbor Transfer Portal are documented here.
The project follows Semantic Versioning for product releases.

## 1.1.0 - 2026-11-27

### Added
- Nexus Docker Registry v2 SOURCE/TARGET (profile-based browse, inspect, export, TARGET import).
- Classic Nexus Helm HTTP (non-OCI) hosted repositories with exact-repository fail-closed writes.
- Type-aware registry profiles and UI labels for Harbor / Nexus Docker / Nexus Helm.
- Bundle v1.1 registry origin metadata with backward compatibility to Harbor v1.0 bundles.

### Fixed
- Helm OCI transport flags for Nexus: `--plain-http`, conditional `--insecure-skip-tls-verify`, `--ca-file`.
- Compact `skopeo inspect` survives very large RepoTags; truncated output raises `skopeo_output_truncated`.
- Immutable import receipt publication is atomic; success receipt only after `COMPLETED`; `skip_conflicts` recorded on all receipt paths.
- Harbor connection probe runs off the event loop.

### Changed
- pytest 9.2 / pytest-asyncio 1.5 and minimatch 11 close the open Dependabot alerts.
- Nexus legacy Docker v2 E2E runs as scoped `Integration` steps; CI scope contract test guards job/gate wiring.

### Acceptance
- Regional acceptance owner-attested on Windows; independent artifacts and sign-offs tracked in #434/#436.

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
- Scoped CI gates covering backend, frontend, protocol/security, Compose and OCI integration, plus clean-host offline installation and isolated SOURCE-to-TARGET acceptance in the `Integration` job and Windows offline lifecycle contracts on `windows-2022`; real Windows Docker Desktop and regional acceptance remain external gates (issue #507).

### Security
- Archive extraction, signing-key handling, registry credentials and transfer paths are validated fail-closed.
- Harbor credentials, JWT secrets and SOURCE private signing keys are excluded from transfer and release archives.
- Conflicting TARGET tags or chart versions are not overwritten without an explicit authorized policy.

### Known limitations
- SOURCE and TARGET remain intentionally disconnected; physical media handling is an operator responsibility.
- Rollback across incompatible database migrations may require restoring a pre-upgrade backup.
- Helm OCI manifest digests may differ after a cross-registry push; signed chart package integrity and verified SOURCE-to-TARGET provenance are used for replay semantics.
