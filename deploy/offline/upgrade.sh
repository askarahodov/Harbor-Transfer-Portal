#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd -P)
PREVIOUS_INPUT=${1:-$SCRIPT_DIR}

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

cd "$SCRIPT_DIR"
[ -f release-version.txt ] || fail 'release-version.txt is missing'
[ -f release-arch.txt ] || fail 'release-arch.txt is missing'
[ -f CHECKSUMS.sha256 ] || fail 'CHECKSUMS.sha256 is missing'
[ -f compose.yaml ] || fail 'compose.yaml is missing'
[ -f backup.sh ] || fail 'backup.sh is missing'
[ -f images/backend.tar ] || fail 'images/backend.tar is missing'
[ -f images/frontend.tar ] || fail 'images/frontend.tar is missing'

require_command sha256sum
require_command docker

printf 'Verifying new offline kit payload...\n'
sha256sum -c CHECKSUMS.sha256

new_version=$(cat release-version.txt)
release_arch=$(cat release-arch.txt)
host_arch=$(normalize_arch "$(uname -m)") || fail "unsupported host architecture: $(uname -m)"
[ "$host_arch" = "$release_arch" ] || fail "release architecture $release_arch does not match host $host_arch"

docker info >/dev/null 2>&1 || fail 'Docker Engine is not available'
docker compose version >/dev/null 2>&1 || fail 'Docker Compose v2 is not available'

[ -d "$PREVIOUS_INPUT" ] || fail "previous install directory not found: $PREVIOUS_INPUT"
PREVIOUS_DIR=$(CDPATH= cd -- "$PREVIOUS_INPUT" && pwd -P)
source_env="$PREVIOUS_DIR/.env"
source_compose="$PREVIOUS_DIR/compose.yaml"
[ -e "$source_env" ] || fail 'previous install .env is missing'
[ -f "$source_env" ] && [ ! -L "$source_env" ] || fail 'previous install .env must be a regular non-symlink file'
[ -f "$source_compose" ] || fail 'previous install compose.yaml is missing'

old_version=$(sed -n 's/^PORTAL_VERSION=//p' "$source_env" | head -n 1)
[ -n "$old_version" ] || fail 'previous install .env has no PORTAL_VERSION'
[ "$old_version" != "$new_version" ] || fail "installation is already configured for version $new_version"

if [ "$PREVIOUS_DIR" != "$SCRIPT_DIR" ] && { [ -e "$SCRIPT_DIR/.env" ] || [ -L "$SCRIPT_DIR/.env" ]; }; then
  fail 'new kit directory already contains .env; refusing to overwrite it'
fi

printf 'Creating mandatory pre-upgrade backup of %s...\n' "$old_version"
backup_archive=$(PORTAL_BACKUP_DIR="${PORTAL_BACKUP_DIR:-$PREVIOUS_DIR/backups}" sh "$SCRIPT_DIR/backup.sh" "$PREVIOUS_DIR")
[ -f "$backup_archive" ] || fail 'backup script did not produce an archive'

printf 'Loading prebuilt images for %s...\n' "$new_version"
docker load -i images/backend.tar
docker load -i images/frontend.tar

backend_image="harbor-transfer-portal-backend:$new_version"
frontend_image="harbor-transfer-portal-frontend:$new_version"
for image in "$backend_image" "$frontend_image"; do
  docker image inspect "$image" >/dev/null 2>&1 || fail "expected local image missing after docker load: $image"
  image_arch=$(docker image inspect --format '{{.Architecture}}' "$image")
  [ "$image_arch" = "$release_arch" ] || fail "local image architecture mismatch for $image: $image_arch"
done

umask 077
rollback_env="$SCRIPT_DIR/.env.rollback.$$"
target_tmp="$SCRIPT_DIR/.env.upgrade.$$"
cp "$source_env" "$rollback_env"
chmod 0600 "$rollback_env"

if ! awk -v version="$new_version" '
  BEGIN { seen = 0 }
  /^PORTAL_VERSION=/ {
    if (seen == 0) {
      print "PORTAL_VERSION=" version
      seen = 1
    }
    next
  }
  { print }
  END { if (seen == 0) exit 3 }
' "$source_env" > "$target_tmp"; then
  rm -f "$rollback_env" "$target_tmp"
  fail 'failed to prepare upgraded .env'
fi
chmod 0600 "$target_tmp"
mv "$target_tmp" "$SCRIPT_DIR/.env"

timeout=${PORTAL_INSTALL_TIMEOUT_SECONDS:-180}
case "$timeout" in
  ''|*[!0-9]*)
    mv "$rollback_env" "$SCRIPT_DIR/.env"
    fail 'PORTAL_INSTALL_TIMEOUT_SECONDS must be an integer'
    ;;
esac

printf 'Starting Harbor Transfer Portal %s with persistent volume preserved...\n' "$new_version"
if docker compose --env-file "$SCRIPT_DIR/.env" -f "$SCRIPT_DIR/compose.yaml" \
  up -d --no-build --pull never --wait --wait-timeout "$timeout"; then
  rm -f "$rollback_env"
  printf '\nUpgrade completed: %s -> %s\n' "$old_version" "$new_version"
  printf 'Pre-upgrade backup: %s\n' "$backup_archive"
  printf 'Persistent project/volume identity remains harbor-transfer-portal.\n'
else
  mv "$rollback_env" "$SCRIPT_DIR/.env"
  printf 'ERROR: startup of version %s failed; .env was restored to version %s.\n' "$new_version" "$old_version" >&2
  printf 'ERROR: database migrations may already have run and are not automatically rolled back.\n' >&2
  printf 'ERROR: inspect logs and preserve the pre-upgrade backup before attempting recovery: %s\n' "$backup_archive" >&2
  exit 2
fi
