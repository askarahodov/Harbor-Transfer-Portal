#!/usr/bin/env sh
set -eu

umask 077

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 2
}

info() {
  printf '%s\n' "$*"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"
}

version_ge() {
  actual=$1
  required=$2
  first=$(printf '%s\n%s\n' "$required" "$actual" | sort -V | head -n 1)
  [ "$first" = "$required" ]
}

read_env_value() {
  key=$1
  file=$2
  awk -F= -v key="$key" '
    $0 !~ /^[[:space:]]*#/ && $1 == key {
      print substr($0, index($0, "=") + 1)
    }
  ' "$file" | tail -n 1
}

set_env_value() {
  key=$1
  value=$2
  file=$3
  tmp="${file}.tmp.$$"
  awk -F= -v key="$key" -v value="$value" '
    BEGIN { replaced = 0 }
    $0 !~ /^[[:space:]]*#/ && $1 == key {
      if (!replaced) {
        print key "=" value
        replaced = 1
      }
      next
    }
    { print }
    END {
      if (!replaced) {
        print key "=" value
      }
    }
  ' "$file" > "$tmp"
  chmod 600 "$tmp"
  mv "$tmp" "$file"
}

normalize_architecture() {
  case "$1" in
    x86_64|amd64) printf 'amd64\n' ;;
    aarch64|arm64) printf 'arm64\n' ;;
    *) fail "unsupported host architecture: $1" ;;
  esac
}

wait_healthy() {
  service=$1
  timeout_seconds=$2
  elapsed=0

  while [ "$elapsed" -lt "$timeout_seconds" ]; do
    container_id=$(docker compose --env-file .env -f compose.yaml ps -q "$service")
    if [ -n "$container_id" ]; then
      status=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id")
      case "$status" in
        healthy)
          info "$service is healthy"
          return 0
          ;;
        unhealthy|exited|dead)
          fail "$service entered terminal state: $status"
          ;;
      esac
    fi
    sleep 2
    elapsed=$((elapsed + 2))
  done

  fail "$service did not become healthy within ${timeout_seconds}s"
}

usage() {
  cat >&2 <<'EOF'
Usage:
  ./install.sh SOURCE
  ./install.sh TARGET

The same offline kit installs either contour. Existing .env is never overwritten.
A different installed version must be handled by the dedicated upgrade procedure.
EOF
  exit 2
}

contour=${1:-}
case "$contour" in
  SOURCE|TARGET) ;;
  *) usage ;;
esac
[ "$#" -eq 1 ] || usage

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$script_dir"

for file in VERSION ARCHITECTURE release.json SHA256SUMS compose.yaml .env.example \
  images/backend.tar images/frontend.tar; do
  [ -f "$file" ] || fail "offline kit file is missing: $file"
done

require_command sha256sum
require_command docker
require_command uname
require_command awk
require_command sed
require_command sort
require_command head
require_command tail
require_command od
require_command tr
require_command du
require_command df
require_command chmod
require_command mv

info "Verifying offline kit checksums..."
sha256sum -c SHA256SUMS

version=$(cat VERSION)
architecture=$(cat ARCHITECTURE)
case "$version" in
  *[!0-9A-Za-z.-]*|'') fail "invalid VERSION in offline kit" ;;
esac
case "$architecture" in
  amd64|arm64) ;;
  *) fail "invalid ARCHITECTURE in offline kit: $architecture" ;;
esac

host_architecture=$(normalize_architecture "$(uname -m)")
[ "$host_architecture" = "$architecture" ] || \
  fail "kit architecture $architecture does not match host $host_architecture"

docker info >/dev/null 2>&1 || fail "Docker Engine is unavailable or current user cannot access it"
docker compose version >/dev/null 2>&1 || fail "Docker Compose plugin is unavailable"

engine_version=$(docker version --format '{{.Server.Version}}' | sed 's/^v//')
compose_version=$(docker compose version --short | sed 's/^v//')
version_ge "$engine_version" "24.0.0" || fail "Docker Engine >=24.0.0 is required; found $engine_version"
version_ge "$compose_version" "2.20.0" || fail "Docker Compose >=2.20.0 is required; found $compose_version"

image_kb=$(du -sk images | awk '{print $1}')
required_kb=$((image_kb * 3 + 524288))
available_kb=$(df -Pk . | awk 'NR == 2 {print $4}')
[ -n "$available_kb" ] || fail "cannot determine available disk space"
[ "$available_kb" -ge "$required_kb" ] || \
  fail "insufficient disk space: need at least ${required_kb} KiB free, have ${available_kb} KiB"

if [ -e .env ]; then
  [ -f .env ] && [ ! -L .env ] || fail "existing .env must be a regular file"
  installed_version=$(read_env_value PORTAL_VERSION .env)
  installed_contour=$(read_env_value PORTAL_CONTOUR .env)
  [ -n "$installed_version" ] || fail "existing .env has no PORTAL_VERSION"
  [ "$installed_version" = "$version" ] || \
    fail "existing installation version is $installed_version; kit is $version. Use upgrade procedure."
  [ -n "$installed_contour" ] || fail "existing .env has no PORTAL_CONTOUR"
  [ "$installed_contour" = "$contour" ] || \
    fail "existing installation contour is $installed_contour; requested $contour"
  info "Existing .env preserved for matching version/contour."
else
  cp .env.example .env
  chmod 600 .env
  set_env_value PORTAL_VERSION "$version" .env
  set_env_value PORTAL_CONTOUR "$contour" .env
  jwt_secret=$(od -An -N48 -tx1 /dev/urandom | tr -d ' \n')
  [ "${#jwt_secret}" -ge 64 ] || fail "failed to generate JWT secret"
  set_env_value JWT_SECRET "$jwt_secret" .env
  info "Created .env with local JWT secret. Harbor credential remains unset by design."
fi

info "Loading prebuilt application images..."
docker load -i images/backend.tar
docker load -i images/frontend.tar

backend_ref="harbor-transfer-portal-backend:${version}"
frontend_ref="harbor-transfer-portal-frontend:${version}"
docker image inspect "$backend_ref" >/dev/null 2>&1 || fail "backend image missing after docker load: $backend_ref"
docker image inspect "$frontend_ref" >/dev/null 2>&1 || fail "frontend image missing after docker load: $frontend_ref"

backend_arch=$(docker image inspect --format '{{.Architecture}}' "$backend_ref")
frontend_arch=$(docker image inspect --format '{{.Architecture}}' "$frontend_ref")
[ "$backend_arch" = "$architecture" ] || fail "backend image architecture mismatch: $backend_arch"
[ "$frontend_arch" = "$architecture" ] || fail "frontend image architecture mismatch: $frontend_arch"

info "Validating offline Compose configuration..."
docker compose --env-file .env -f compose.yaml config -q

info "Starting Harbor Transfer Portal without build or pull..."
docker compose --env-file .env -f compose.yaml up -d --no-build

health_timeout=${INSTALL_HEALTH_TIMEOUT_SECONDS:-120}
case "$health_timeout" in
  ''|*[!0-9]*) fail "INSTALL_HEALTH_TIMEOUT_SECONDS must be an integer" ;;
esac
[ "$health_timeout" -ge 10 ] || fail "INSTALL_HEALTH_TIMEOUT_SECONDS must be >=10"

wait_healthy backend "$health_timeout"
wait_healthy frontend "$health_timeout"

http_port=$(read_env_value PORTAL_HTTP_PORT .env)
[ -n "$http_port" ] || http_port=8080

cat <<EOF

Harbor Transfer Portal $version installed successfully for contour $contour.
URL: http://localhost:$http_port

Next steps:
1. Create the local bootstrap admin without storing its password in .env:
   export BOOTSTRAP_ADMIN_PASSWORD='<strong password>'
   docker compose --env-file .env -f compose.yaml exec -T \
     -e BOOTSTRAP_ADMIN_PASSWORD="\$BOOTSTRAP_ADMIN_PASSWORD" \
     backend python -m app.auth.cli --username admin
   unset BOOTSTRAP_ADMIN_PASSWORD
2. Sign in as admin and configure only this contour's local Harbor in Settings.
3. Keep TLS verification enabled; upload the local CA in Settings when private PKI is used.
4. SOURCE: configure/generate the signing key locally. TARGET: install only trusted SOURCE public key(s).

Re-running ./install.sh $contour for the same version preserves .env and the named portal-data volume.
EOF
