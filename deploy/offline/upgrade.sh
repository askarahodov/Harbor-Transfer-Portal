#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd -P)
PREVIOUS_INPUT=${1:-$SCRIPT_DIR}
old_quiesced=0
target_tmp=

cleanup() {
  status=$?
  if [ "$old_quiesced" -eq 1 ] && [ -n "${PREVIOUS_DIR:-}" ]; then
    if docker compose --env-file "$PREVIOUS_DIR/.env" -f "$PREVIOUS_DIR/compose.yaml" \
      unpause >/dev/null 2>&1; then
      :
    else
      printf 'ERROR: failed to unpause previous Portal after aborted upgrade pre-start phase\n' >&2
      [ "$status" -ne 0 ] || status=1
    fi
  fi
  if [ -n "$target_tmp" ]; then
    rm -f "$target_tmp"
  fi
  trap - 0 HUP INT TERM
  exit "$status"
}
trap cleanup 0 HUP INT TERM

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
previous_restore="$PREVIOUS_DIR/restore.sh"
previous_checksums="$PREVIOUS_DIR/CHECKSUMS.sha256"
previous_release_version="$PREVIOUS_DIR/release-version.txt"
previous_release_arch="$PREVIOUS_DIR/release-arch.txt"
[ -e "$source_env" ] || fail 'previous install .env is missing'
[ -f "$source_env" ] && [ ! -L "$source_env" ] || fail 'previous install .env must be a regular non-symlink file'
[ -f "$source_compose" ] || fail 'previous install compose.yaml is missing'
[ -x "$previous_restore" ] && [ ! -L "$previous_restore" ] ||   fail 'previous install restore.sh is missing, non-executable, or a symlink'
[ -f "$previous_checksums" ] && [ ! -L "$previous_checksums" ] ||   fail 'previous install CHECKSUMS.sha256 is missing or invalid'
[ -f "$previous_release_version" ] || fail 'previous install release-version.txt is missing'
[ -f "$previous_release_arch" ] || fail 'previous install release-arch.txt is missing'
[ -f "$PREVIOUS_DIR/images/backend.tar" ] || fail 'previous backend image archive is missing'
[ -f "$PREVIOUS_DIR/images/frontend.tar" ] || fail 'previous frontend image archive is missing'

printf 'Verifying previous offline kit recovery payload...\n'
(
  cd "$PREVIOUS_DIR"
  sha256sum -c CHECKSUMS.sha256
)
grep -F '  restore.sh' "$previous_checksums" >/dev/null ||   fail 'previous restore.sh is not covered by previous kit checksums'

old_version=$(sed -n 's/^PORTAL_VERSION=//p' "$source_env" | head -n 1)
[ -n "$old_version" ] || fail 'previous install .env has no PORTAL_VERSION'
previous_version=$(cat "$previous_release_version")
[ "$previous_version" = "$old_version" ] ||   fail "previous kit/.env version mismatch: kit=$previous_version env=$old_version"
previous_arch=$(cat "$previous_release_arch")
[ "$previous_arch" = "$host_arch" ] ||   fail "previous release architecture $previous_arch does not match host $host_arch"
[ "$old_version" != "$new_version" ] || fail "installation is already configured for version $new_version"

if [ "$PREVIOUS_DIR" != "$SCRIPT_DIR" ] && { [ -e "$SCRIPT_DIR/.env" ] || [ -L "$SCRIPT_DIR/.env" ]; }; then
  fail 'new kit directory already contains .env; refusing to overwrite it'
fi

printf 'Creating mandatory pre-upgrade backup of %s...\n' "$old_version"
backup_archive=$(PORTAL_BACKUP_DIR="${PORTAL_BACKUP_DIR:-$PREVIOUS_DIR/backups}" \
  PORTAL_BACKUP_LEAVE_PAUSED=1 \
  sh "$SCRIPT_DIR/backup.sh" "$PREVIOUS_DIR")
[ -f "$backup_archive" ] || fail 'backup script did not produce an archive'
old_quiesced=1

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
target_tmp=$(mktemp "$SCRIPT_DIR/.env.upgrade.XXXXXX")

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
  fail 'failed to prepare upgraded .env'
fi
chmod 0600 "$target_tmp"
mv "$target_tmp" "$SCRIPT_DIR/.env"
target_tmp=

timeout=${PORTAL_INSTALL_TIMEOUT_SECONDS:-180}
case "$timeout" in
  ''|*[!0-9]*)
    rm -f "$SCRIPT_DIR/.env"
    fail 'PORTAL_INSTALL_TIMEOUT_SECONDS must be an integer'
    ;;
esac

printf 'Starting Harbor Transfer Portal %s with persistent volume preserved...\n' "$new_version"
if docker compose --env-file "$SCRIPT_DIR/.env" -f "$SCRIPT_DIR/compose.yaml" \
  up -d --no-build --pull never --wait --wait-timeout "$timeout"; then
  old_quiesced=0
  trap - 0 HUP INT TERM
  printf '\nUPGRADE_OK: %s -> %s\n' "$old_version" "$new_version"
  printf 'Pre-upgrade backup: %s\n' "$backup_archive"
  printf 'Persistent project/volume identity remains harbor-transfer-portal.\n'
else
  old_quiesced=0
  rm -f "$SCRIPT_DIR/.env"
  printf 'ERROR: startup of version %s failed; starting automatic matching-version rollback.\n' "$new_version" >&2
  printf 'ERROR: rollback source backup: %s\n' "$backup_archive" >&2

  if sh "$previous_restore" "$backup_archive" --confirm-restore "$PREVIOUS_DIR"; then
    printf 'ROLLBACK_OK: restored Harbor Transfer Portal %s from pre-upgrade backup.\n' "$old_version" >&2
    printf 'ROLLBACK_OK: active installation remains %s\n' "$PREVIOUS_DIR" >&2
    printf 'ROLLBACK_OK: backup preserved at %s\n' "$backup_archive" >&2
    trap - 0 HUP INT TERM
    exit 2
  fi

  printf 'ROLLBACK_FAILED: automatic recovery of version %s failed.\n' "$old_version" >&2
  printf 'ROLLBACK_FAILED: do not start either release against the current persistent volume.\n' >&2
  printf 'ROLLBACK_FAILED: preserve and recover from backup %s using matching kit %s\n'     "$backup_archive" "$PREVIOUS_DIR" >&2
  exit 3
fi
