# Harbor Transfer Portal

Harbor Transfer Portal is an air-gap artifact transfer application for moving container images and Helm OCI charts between two physically and network-isolated environments.

## Core operating model

There are two independent installations:

- **SOURCE** runs inside the source isolated contour and can communicate only with the Harbor available in that contour.
- **TARGET** runs inside the target isolated contour and can communicate only with the Harbor available in that contour.
- There is **no network connection between SOURCE and TARGET**.
- Artifact exchange happens through a portable, verifiable bundle moved by an approved offline transport process.

The portal must never depend on direct Harbor-to-Harbor replication across the isolation boundary.

## Planned architecture

```text
backend/   Python/FastAPI API, domain logic and transfer services
frontend/  Vue/Vite administrative UI
docs/      architecture, protocol and operational documentation
data/      local runtime state; generated contents are ignored
deploy/    deployment and offline packaging assets
```

The planned backend integrates with the local Harbor API, Skopeo for container-image transport, and Helm OCI for chart transport. Bundle creation and import include explicit metadata, checksums and verification so a successful process exit is not treated as proof of a successful delivery.

## Development quick start

Prerequisites for later implementation stages:

- GNU Make
- Docker with Docker Compose v2
- Python 3.12+
- Node.js 22+
- npm

Prepare local configuration:

```bash
cp .env.example .env
```

Common commands:

```bash
make up
make logs
make lint
make test
make build
make down
```

At the repository-foundation stage implementation-backed targets intentionally report missing backend/frontend/compose files. Missing checks must never silently pass.

## Engineering principles

- No credentials or private keys in Git.
- No internet dependency is allowed for runtime operation in the closed contours.
- Each installation talks only to its local Harbor.
- Subprocesses must use structured argument arrays rather than shell interpolation of untrusted input.
- TLS verification is explicit; custom CA support is preferred over disabling verification.
- Tests and linters must fail the build when they fail.
- Development uses scoped tests for touched areas; the complete required CI suite is the merge checkpoint.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow and [docs/decisions.md](docs/decisions.md) for architectural decisions.
