# Changelog

All notable user-facing changes to Harbor Transfer Portal are documented here.
The project follows Semantic Versioning for product releases.

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
