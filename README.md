# Harbor Transfer Portal

Harbor Transfer Portal is an air-gap artifact transfer application for moving container images and Helm OCI charts between two physically and network-isolated environments.

## Core operating model

There are two independent installations:

- **SOURCE** runs inside the source isolated contour and can communicate only with the Harbor available in that contour.
- **TARGET** runs inside the target isolated contour and can communicate only with the Harbor available in that contour.
- There is **no network connection between SOURCE and TARGET**.
- Artifact exchange happens through a portable, verifiable bundle moved by an approved offline transport process.

The portal must never depend on direct Harbor-to-Harbor replication across the isolation boundary.

## Architecture

```text
backend/   Python/FastAPI API, domain logic and transfer services
frontend/  Vue/Vite administrative UI
docs/      architecture, protocol and operational documentation
data/      local runtime state; generated contents are ignored
deploy/    deployment and offline packaging assets
```

The backend will integrate with the local Harbor API, Skopeo for container-image transport, and Helm OCI for chart transport. Bundle creation and import include explicit metadata, checksums and verification so a successful process exit is not treated as proof of a successful delivery.

## Development quick start

Prerequisites:

- GNU Make
- Python 3.12
- Docker with Docker Compose v2 for later deployment stages
- Node.js 22+ and npm for the later frontend stage

Prepare local configuration:

```bash
cp .env.example .env
```

### Backend

Create an isolated Python environment and install the backend with development dependencies:

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e './backend[dev]'
```

Start the API from `backend/`:

```bash
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Current local endpoints:

```text
GET /api/health
GET /api/ready
GET /docs
```

Run scoped backend checks from the repository root:

```bash
make lint-backend
make test-backend
```

Repository-wide commands remain strict: if a later-stage component such as frontend or Compose is not implemented yet, the corresponding all-project target fails explicitly instead of silently skipping it.

## Configuration

`PORTAL_CONTOUR` accepts only `SOURCE` or `TARGET`. Each installation uses one neutral set of `HARBOR_*` settings for its local Harbor; SOURCE and TARGET credentials are never configured together in one portal instance.

Health and readiness endpoints do not expose Harbor credentials or other secret configuration.

## Engineering principles

- No credentials or private keys in Git.
- No internet dependency is allowed for runtime operation in the closed contours.
- Each installation talks only to its local Harbor.
- Subprocesses must use structured argument arrays rather than shell interpolation of untrusted input.
- TLS verification is explicit; custom CA support is preferred over disabling verification.
- Tests and linters must fail the build when they fail.
- Development uses scoped tests for touched areas; the complete required CI suite is the merge checkpoint.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow and [docs/decisions.md](docs/decisions.md) for architectural decisions.
