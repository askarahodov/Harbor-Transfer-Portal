# Architecture decision index

Architectural decisions are recorded before implementation when they affect protocol compatibility, security boundaries, persistence, transfer verification, deployment or multiple workstreams.

| ID | Decision | Status |
| --- | --- | --- |
| ADR-001 | SOURCE/TARGET isolation model and trust boundary | Planned |
| ADR-002 | Portable bundle format, manifest and checksum/signature model | Planned |
| ADR-003 | Container image payload representation and multi-architecture handling | Planned |
| ADR-004 | Authentication, authorization and local user/session model | Planned |
| ADR-005 | Harbor TLS/custom CA and secret storage policy | Planned |
| ADR-006 | Import conflict/idempotency policy | Planned |
| ADR-007 | Offline installation and dependency packaging model | Planned |
| [ADR-008](adr/ADR-008-frontend-ui-kit.md) | Frontend UI kit: Element Plus + Lucide | Accepted |

## Decision record template

Each ADR should state:

- context and problem;
- constraints, especially air-gap and security constraints;
- considered options;
- chosen decision;
- consequences and trade-offs;
- verification/testing implications;
- migration/compatibility impact when applicable.

Do not silently change an accepted cross-cutting decision in an implementation PR. Update or supersede the ADR explicitly.
