#!/usr/bin/env sh
set -eu

cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || fail 'Docker CLI missing; install Docker Engine and Compose v2'
docker_os=$(docker info --format '{{.OSType}}' 2>/dev/null) || fail 'Docker Engine unavailable; check daemon and Docker permissions'
[ "$docker_os" = linux ] || fail 'Docker must run Linux containers'
docker compose version >/dev/null 2>&1 || fail 'Docker Compose v2 is required'

if [ ! -e .env ]; then
    [ -f .env.example ] || fail '.env.example is missing'
    cp .env.example .env
    chmod 600 .env
    printf '%s\n' 'Created .env from template; backend will persist a random JWT secret.'
fi
[ -f .env ] && [ ! -L .env ] || fail '.env must be a regular non-symlink file'
docker compose config -q || fail 'Compose configuration invalid; check .env and compose.yaml'

timeout=180
if [ -n "${PORTAL_INSTALL_TIMEOUT_SECONDS:-}" ]; then
    timeout=${PORTAL_INSTALL_TIMEOUT_SECONDS}
fi
case "$timeout" in
    ''|*[!0-9]*|0) fail 'PORTAL_INSTALL_TIMEOUT_SECONDS must be a positive integer' ;;
esac

revision=$(git rev-parse HEAD 2>/dev/null || printf '%s' unknown)
if PORTAL_VCS_REF="$revision" docker compose up -d --build --force-recreate --wait --wait-timeout "$timeout"; then
    :
else
    printf '%s\n' 'ERROR: Portal failed to become healthy; inspect docker compose logs --tail=100' >&2
    docker compose ps >&2 || true
    exit 1
fi

http_port=$(sed -n 's/^PORTAL_HTTP_PORT=//p' .env | head -n 1)
[ -n "$http_port" ] || http_port=8080
printf 'Portal is healthy: http://127.0.0.1:%s/\n' "$http_port"
printf '%s\n' 'New database only: sign in with admin/admin and immediately change the password.'
