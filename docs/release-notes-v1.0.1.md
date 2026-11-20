# Harbor Transfer Portal v1.0.1 — upgrade acceptance candidate

**Status: TEST ONLY — not a production release.**

This stable-numeric candidate exists solely to exercise the real offline upgrade path from v1.0.0. It must not be tagged, published as a release, or deployed to production.

## Candidate contents

- Windows PowerShell 5.1+ lifecycle wrappers for Windows 10/11 hosts managing the Linux-container runtime through Docker Desktop.
- Versioned, checksummed offline-kit entrypoints for install, start, stop, backup, restore, upgrade and uninstall.
- Release manifest and OCI image labels bound to this candidate version and source revision.
- The same Harbor Transfer Portal SOURCE/TARGET application behavior as the PR #548 acceptance candidate.

## Runtime boundary

This does not introduce Windows-native containers or a Windows-native application runtime. The application containers remain Linux containers; Windows is the host environment through Docker Desktop Linux containers.

## Acceptance boundary

Use only on a disposable Windows VM with a v1.0.0 test installation and no production data. Verify backup, upgrade, health, persisted data and rollback before considering any release. The candidate is intentionally untagged and unpublished.
