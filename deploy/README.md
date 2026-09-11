# Docker Compose runtime

This directory documents the single-instance SOURCE/TARGET runtime introduced by issue #5.

## Runtime model

One deployment contains exactly two application services:

- `backend`: FastAPI plus Skopeo and Helm, reachable only on the internal Compose network;
- `frontend`: Nginx serving the compiled SPA and proxying `/api/` to the backend.

Only the frontend publishes a host port. The browser therefore uses same-origin API requests and does not require CORS in the normal Compose topology.

The same images run in both contours. Set only `PORTAL_CONTOUR=SOURCE` or `PORTAL_CONTOUR=TARGET`; each backend receives credentials for its local Harbor only.

## Persistent data

The named Docker volume `portal-data` is mounted at `/app/data`. The backend image initializes these reserved paths:

- `/app/data/database` — SQLite database once issue #7 introduces persistence;
- `/app/data/packages` — controlled package workspace;
- `/app/data/incoming` — verified/discovered incoming bundle area;
- `/app/data/outgoing` — completed SOURCE bundles;
- `/app/data/logs` — persisted application logs when enabled;
- `/app/data/receipts` — import receipts/reports;
- `/app/data/tmp` — operation-local temporary workspace.

`docker compose down` preserves the named volume. `docker compose down -v` deliberately deletes it and must not be used when data must be retained.

Large removable-media discovery/bind-mount policy is intentionally deferred to the TARGET intake workflow (#19); the foundation does not expose arbitrary host paths to the application.

## Build-time dependencies

Runtime containers do not download software or contact the internet at startup. Building images currently requires access to the following external sources:

- Docker base images: `python:3.12.14-slim-bookworm`, `node:22.23.2-alpine3.24`, `nginx:1.30.1-alpine`;
- Debian bookworm package repositories for `skopeo=1.9.3+ds1-1+b10`, CA certificates, tar and gzip;
- Python package indexes for backend dependencies from `backend/pyproject.toml`;
- npm registry for frontend dependencies from `frontend/package.json`;
- `get.helm.sh` for Helm `v3.22.0` during backend image build only.

Helm archives are verified against architecture-specific SHA256 values before installation. Supported backend build architectures in this foundation are `linux/amd64` and `linux/arm64`.

The final offline release (#28) must ship prebuilt images so closed contours only load images and never repeat these online build steps.

## Start

```bash
cp .env.example .env
# edit .env: local contour and local Harbor credentials only
docker compose config
docker compose up -d --build
```

Open `http://localhost:${PORTAL_HTTP_PORT:-8080}`. The proxied backend health endpoint is `/api/health`; Nginx itself exposes `/healthz` for container health checks.

## Validation

A scoped smoke check builds the stack, verifies both health paths and the `/api/` proxy, checks Skopeo/Helm versions, restarts services and verifies the named-volume marker survived:

```bash
./deploy/smoke-compose.sh
```

The smoke script stops containers on exit but preserves the named volume.

After images are built or loaded locally, normal `docker compose up -d`/restart does not require internet access.
