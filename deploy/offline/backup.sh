#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd -P)
INSTALL_INPUT=${1:-$SCRIPT_DIR}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 2
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"
}

[ -d "$INSTALL_INPUT" ] || fail "install directory not found: $INSTALL_INPUT"
INSTALL_DIR=$(CDPATH= cd -- "$INSTALL_INPUT" && pwd -P)
cd "$INSTALL_DIR"

[ -f compose.yaml ] || fail 'compose.yaml is missing from install directory'
[ -e .env ] || fail '.env is missing from install directory'
[ -f .env ] && [ ! -L .env ] || fail 'existing .env must be a regular non-symlink file'

require_command docker
require_command sha256sum
require_command tar
require_command date

docker info >/dev/null 2>&1 || fail 'Docker Engine is not available'
docker compose version >/dev/null 2>&1 || fail 'Docker Compose v2 is not available'

version=$(sed -n 's/^PORTAL_VERSION=//p' .env | head -n 1)
[ -n "$version" ] || fail 'existing .env has no PORTAL_VERSION'
backend_image="harbor-transfer-portal-backend:$version"
docker image inspect "$backend_image" >/dev/null 2>&1 || fail "current local backend image not found: $backend_image"

volume_name=harbor-transfer-portal_portal-data
docker volume inspect "$volume_name" >/dev/null 2>&1 || fail "persistent volume not found: $volume_name"

backup_root=${PORTAL_BACKUP_DIR:-"$INSTALL_DIR/backups"}
[ ! -L "$backup_root" ] || fail 'backup directory must not be a symlink'
mkdir -p "$backup_root"
[ -d "$backup_root" ] || fail 'backup path is not a directory'
chmod 0700 "$backup_root"
backup_root=$(CDPATH= cd -- "$backup_root" && pwd -P)

umask 077
timestamp=$(date -u '+%Y%m%dT%H%M%SZ')
archive="$backup_root/harbor-transfer-portal-backup-v${version}-${timestamp}.tar.gz"
[ ! -e "$archive" ] || fail "backup already exists: $archive"

tmp=$(mktemp -d "$backup_root/.htp-backup.XXXXXX")
paused=0

cleanup() {
  status=$?
  if [ "$paused" -eq 1 ]; then
    if docker compose --env-file "$INSTALL_DIR/.env" -f "$INSTALL_DIR/compose.yaml" unpause >/dev/null 2>&1; then
      :
    else
      printf 'ERROR: failed to unpause portal after backup attempt\n' >&2
      [ "$status" -ne 0 ] || status=1
    fi
  fi
  rm -rf "$tmp"
  trap - 0 HUP INT TERM
  exit "$status"
}
trap cleanup 0 HUP INT TERM

running_ids=$(docker compose --env-file "$INSTALL_DIR/.env" -f "$INSTALL_DIR/compose.yaml" ps --status running -q)
if [ -n "$running_ids" ]; then
  printf 'Pausing running portal services for a consistent snapshot...\n' >&2
  docker compose --env-file "$INSTALL_DIR/.env" -f "$INSTALL_DIR/compose.yaml" pause >/dev/null
  paused=1
fi

printf 'Creating persistent data snapshot...\n' >&2
docker run --rm --pull never --network none \
  --entrypoint tar \
  -v "$volume_name:/app/data:ro" \
  "$backend_image" \
  -C /app/data -czf - . > "$tmp/portal-data.tar.gz"

cp "$INSTALL_DIR/.env" "$tmp/.env"
chmod 0600 "$tmp/.env" "$tmp/portal-data.tar.gz"
cat > "$tmp/backup-metadata.txt" <<EOF
product=harbor-transfer-portal
version=$version
created_at_utc=$timestamp
volume=$volume_name
EOF
chmod 0600 "$tmp/backup-metadata.txt"
(
  cd "$tmp"
  sha256sum .env backup-metadata.txt portal-data.tar.gz > CHECKSUMS.sha256
)
chmod 0600 "$tmp/CHECKSUMS.sha256"

tar -C "$tmp" -czf "$archive.tmp" .
chmod 0600 "$archive.tmp"
mv "$archive.tmp" "$archive"
(
  cd "$backup_root"
  sha256sum "$(basename "$archive")" > "$(basename "$archive").sha256"
)
chmod 0600 "$archive.sha256"

if [ "$paused" -eq 1 ]; then
  docker compose --env-file "$INSTALL_DIR/.env" -f "$INSTALL_DIR/compose.yaml" unpause >/dev/null
  paused=0
fi

printf 'Backup created (contains secrets; protect it accordingly): %s\n' "$archive" >&2
# The final stdout line is intentionally machine-readable for upgrade.sh.
printf '%s\n' "$archive"
