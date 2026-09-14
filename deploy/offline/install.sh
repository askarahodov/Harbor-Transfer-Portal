#!/bin/sh
set -eu

cd "$(dirname "$0")"

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 2
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"
}

normalize_arch() {
  case "$1" in
    x86_64|amd64) printf '%s\n' amd64 ;;
    aarch64|arm64) printf '%s\n' arm64 ;;
    *) return 1 ;;
  esac
}

[ -f release-version.txt ] || fail 'release-version.txt is missing'
[ -f release-arch.txt ] || fail 'release-arch.txt is missing'
[ -f CHECKSUMS.sha256 ] || fail 'CHECKSUMS.sha256 is missing'
[ -f compose.yaml ] || fail 'compose.yaml is missing'
[ -f .env.example ] || fail '.env.example is missing'
[ -f images/backend.tar ] || fail 'images/backend.tar is missing'
[ -f images/frontend.tar ] || fail 'images/frontend.tar is missing'

require_command sha256sum
require_command docker

printf 'Verifying offline kit payload...\n'
sha256sum -c CHECKSUMS.sha256

version=$(cat release-version.txt)
release_arch=$(cat release-arch.txt)
host_arch=$(normalize_arch "$(uname -m)") || fail "unsupported host architecture: $(uname -m)"
[ "$host_arch" = "$release_arch" ] || fail "release architecture $release_arch does not match host $host_arch"

docker info >/dev/null 2>&1 || fail 'Docker Engine is not available'
docker compose version >/dev/null 2>&1 || fail 'Docker Compose v2 is not available'

required_bytes=$(( $(wc -c < images/backend.tar) + $(wc -c < images/frontend.tar) + 536870912 ))
available_kb=$(df -Pk . | awk 'NR==2 {print $4}')
case "$available_kb" in
  ''|*[!0-9]*) fail 'cannot determine available disk space' ;;
esac
required_kb=$(( (required_bytes + 1023) / 1024 ))
[ "$available_kb" -ge "$required_kb" ] || fail "not enough disk space: need at least ${required_kb} KiB free"

if [ -f .env ]; then
  configured_version=$(sed -n 's/^PORTAL_VERSION=//p' .env | head -n 1)
  [ -n "$configured_version" ] || fail 'existing .env has no PORTAL_VERSION'
  [ "$configured_version" = "$version" ] || fail "existing .env is configured for version $configured_version, kit version is $version"
  printf 'Existing .env preserved.\n'
else
  contour=${PORTAL_CONTOUR:-SOURCE}
  case "$contour" in
    SOURCE|TARGET) ;;
    *) fail 'PORTAL_CONTOUR must be SOURCE or TARGET' ;;
  esac

  if command -v openssl >/dev/null 2>&1; then
    jwt_secret=$(openssl rand -hex 32)
  else
    jwt_secret=$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')
  fi
  [ ${#jwt_secret} -ge 64 ] || fail 'failed to generate JWT secret'

  awk -v version="$version" -v contour="$contour" -v secret="$jwt_secret" '
    /^PORTAL_CONTOUR=/ { print "PORTAL_CONTOUR=" contour; next }
    /^PORTAL_VERSION=/ { print "PORTAL_VERSION=" version; next }
    /^# JWT_SECRET=$/ { print "JWT_SECRET=" secret; next }
    { print }
  ' .env.example > .env.tmp
  chmod 0600 .env.tmp
  mv .env.tmp .env
  printf 'Created .env for contour %s. Existing files are never overwritten.\n' "$contour"
fi

printf 'Loading prebuilt images...\n'
docker load -i images/backend.tar
docker load -i images/frontend.tar

timeout=${PORTAL_INSTALL_TIMEOUT_SECONDS:-180}
case "$timeout" in
  ''|*[!0-9]*) fail 'PORTAL_INSTALL_TIMEOUT_SECONDS must be an integer' ;;
esac

printf 'Starting Harbor Transfer Portal %s...\n' "$version"
docker compose --env-file .env -f compose.yaml up -d --wait --wait-timeout "$timeout"

http_port=$(sed -n 's/^PORTAL_HTTP_PORT=//p' .env | head -n 1)
[ -n "$http_port" ] || http_port=8080
contour=$(sed -n 's/^PORTAL_CONTOUR=//p' .env | head -n 1)
printf '\nInstallation completed.\n'
printf 'Portal: http://127.0.0.1:%s\n' "$http_port"
printf 'Contour: %s\n' "$contour"
printf 'Next: configure local Harbor credentials/CA and signing/trust keys in the admin UI.\n'
