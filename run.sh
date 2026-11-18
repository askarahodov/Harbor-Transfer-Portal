#!/usr/bin/env sh
# Cross-platform source checkout launcher (Linux / Docker Desktop Linux engine).
# Production offline releases use deploy/offline/install.sh instead.
set -eu

cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 2
}

usage() {
  cat <<'HELP'
Harbor Transfer Portal — source checkout
  ./run.sh             first start / rebuild after git update (build + start)
  ./run.sh build       build images only, preserve containers and data
  ./run.sh start       start existing images WITHOUT building; wait for health
  ./run.sh doctor      check Git, Docker Engine and Compose v2
  ./run.sh status      list services and health
  ./run.sh logs        follow service logs (Ctrl+C to stop watching)
  ./run.sh stop        stop containers, KEEP persistent volumes
  ./run.sh help        show this help

Use separate .env, COMPOSE_PROJECT_NAME and PORTAL_VERSION for a test RC.
Never use 'docker compose down -v' on an installation with retained data.
HELP
}

action=${1:-up}
[ "$#" -le 1 ] || { usage; exit 2; }

case "$action" in
  up|build|start|doctor|status|logs|stop) ;;
  help|-h|--help) usage; exit 0 ;;
  *) usage; fail "unknown command: $action" ;;
esac

require() {
  command -v "$1" >/dev/null 2>&1 || fail "$1 not found on PATH"
}

revision() {
  git rev-parse --verify HEAD 2>/dev/null || fail 'Cannot determine Git SHA; use a Git checkout'
}

doctor() {
  require git
  require docker
  git --version
  docker --version
  docker compose version || fail 'Docker Compose v2 is required'
  docker info >/dev/null 2>&1 || fail 'Docker Engine not running; start Docker Desktop or Docker service'
  docker_os=$(docker info --format '{{.OSType}}') || fail 'Cannot inspect Docker engine'
  [ "$docker_os" = linux ] || fail 'Switch Docker Desktop to Linux containers'
  printf 'Docker engine: linux containers OK\n'
  if [ -f .env ] && [ ! -L .env ]; then
    printf '.env: existing configuration kept\n'
  elif [ -e .env ] || [ -L .env ]; then
    fail '.env must be a regular file (not directory/symlink)'
  else
    printf '.env: not created yet (first up will create it)\n'
  fi
}

ensure_env() {
  [ -e .env ] || [ -L .env ] || {
    [ -f .env.example ] || fail 'Missing .env.example'
    (umask 077; cp .env.example .env) || fail 'Failed to create .env'
    printf 'Created .env from template (existing values will not be overwritten).\n'
  }
  [ -f .env ] && [ ! -L .env ] || fail '.env must be a regular non-symlink file'
}

show_url() {
  port=${PORTAL_HTTP_PORT:-$(sed -n 's/^PORTAL_HTTP_PORT=//p' .env | head -n 1)}
  bind=${PORTAL_HTTP_BIND:-$(sed -n 's/^PORTAL_HTTP_BIND=//p' .env | head -n 1)}
  port=${port:-8080}
  bind=${bind:-127.0.0.1}
  printf 'Portal listener: http://%s:%s/  (Docs: /docs/)\n' "$bind" "$port"
  printf 'On a NEW database: login admin/admin and change the password immediately.\n'
}

build_images() {
  printf 'Building images for Git revision %s...\n' "$vcs"
  PORTAL_VCS_REF="$vcs" docker compose build || fail \
    'Image build failed. Check Docker disk, DNS/proxy and BuildKit logs; retry ./run.sh build, then ./run.sh start'
}

start_images() {
  printf 'Starting prebuilt images (no build, no pull) ...\n'
  PORTAL_VCS_REF="$vcs" docker compose up -d --no-build --pull never --wait --wait-timeout 180 || fail \
    'Startup/health check failed. Run ./run.sh status and ./run.sh logs; build missing images with ./run.sh build'
  PORTAL_VCS_REF="$vcs" docker compose ps
  show_url
  printf 'Check UI revision matches %s (start does not rebuild stale images).\n' "$vcs"
}

doctor
if [ "$action" = doctor ]; then exit 0; fi
ensure_env
vcs=$(revision)

case "$action" in
  up) build_images; start_images ;;
  build) build_images; printf 'Build complete. Start with ./run.sh start\n' ;;
  start) start_images ;;
  status) PORTAL_VCS_REF="$vcs" docker compose ps ;;
  logs) PORTAL_VCS_REF="$vcs" docker compose logs -f ;;
  stop) PORTAL_VCS_REF="$vcs" docker compose down; printf 'Stopped. Persistent volume preserved.\n' ;;
esac
