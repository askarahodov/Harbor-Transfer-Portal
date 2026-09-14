# Harbor Transfer Portal v1.0.0 — release notes

Harbor Transfer Portal v1.0.0 is the first release intended for controlled offline transfer of container images and Helm OCI charts between physically and network-isolated Harbor contours.

## What is included

- Independent SOURCE and TARGET deployments from the same offline release payload.
- Harbor browsing and artifact selection on SOURCE.
- Container image export/import through production Skopeo adapters.
- Helm OCI chart export/import through production Helm CLI adapters.
- Signed Bundle Protocol v1 with canonical manifest, Ed25519 signature and SHA-256 payload verification.
- Physical-transfer boundary that requires only the bundle, its `.sha256` sidecar and allowed SOURCE public trust material.
- TARGET verification, preview and default-deny conflict handling before registry mutation.
- Idempotent replay: already verified artifacts are skipped safely.
- Immutable import receipts, operation history and CSV/PDF reports.
- Server-side `admin`, `operator` and `viewer` authorization.
- Offline installation, backup, restore, upgrade and uninstall helpers.

## Release identity

The running product version is available from `/api/health`, displayed in the authenticated UI and recorded in SOURCE bundle metadata. The offline kit records the same value in `release-version.txt` and `release-manifest.json`; release image labels and kit metadata are validated before packaging.

## Installation model

The release archive contains prebuilt backend/frontend images and does not rebuild application images inside the closed contour. Follow `README.md` inside the offline kit. Configure exactly one local Harbor per portal instance and provide contour-specific key material after installation.

## Security notes

- Do not place Harbor credentials, JWT secrets or SOURCE private signing keys on physical transfer media.
- TARGET must trust only approved SOURCE public signing keys.
- Keep TLS verification enabled for real Harbor endpoints; local plain HTTP is used only by explicit CI fixtures.
- A conflicting existing TARGET tag/version is denied by default. Overwrite requires an explicit authorized action.
- Tampered bundles are rejected before registry mutation.

## Known limitations

- The product does not create network connectivity between contours; physical media transport and custody remain operational responsibilities.
- Database migrations are not guaranteed to be backwards compatible. Take a backup before upgrade and use the documented restore procedure when rollback is required.
- A Helm OCI manifest digest can change when the same signed `.tgz` is pushed into another registry. v1.0.0 therefore verifies signed chart payload integrity and persists verified SOURCE-to-TARGET digest provenance for replay/conflict decisions.

## Upgrade and recovery

Before upgrading, run the bundled backup helper and retain the release kit used by the currently installed version. See `docs/offline-lifecycle.md` and the offline kit README for backup, restore, upgrade and rollback constraints.
