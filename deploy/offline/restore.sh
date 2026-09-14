#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd -P)
CALL_DIR=$(pwd -P)
BACKUP_INPUT=${1:-}
CONFIRM=${2:-}
INSTALL_INPUT=${3:-$SCRIPT_DIR}

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

[ -n "$BACKUP_INPUT" ] && [ "$CONFIRM" = "--confirm-restore" ] || \
  fail 'usage: ./restore.sh BACKUP.tar.gz --confirm-restore [INSTALL_DIR]'

case "$BACKUP_INPUT" in
  /*) ;;
  *) BACKUP_INPUT="$CALL_DIR/$BACKUP_INPUT" ;;
esac
case "$INSTALL_INPUT" in
  /*) ;;
  *) INSTALL_INPUT="$CALL_DIR/$INSTALL_INPUT" ;;
esac

[ -f "$BACKUP_INPUT" ] && [ ! -L "$BACKUP_INPUT" ] || \
  fail 'backup must be a regular non-symlink file'
[ -f "$BACKUP_INPUT.sha256" ] && [ ! -L "$BACKUP_INPUT.sha256" ] || \
  fail 'backup .sha256 sidecar is required'
[ -d "$INSTALL_INPUT" ] && [ ! -L "$INSTALL_INPUT" ] || \
  fail 'install directory must be a non-symlink directory'
INSTALL_DIR=$(CDPATH= cd -- "$INSTALL_INPUT" && pwd -P)
cd "$INSTALL_DIR"

[ -f compose.yaml ] || fail 'compose.yaml is missing from install directory'
[ -f release-version.txt ] || fail 'release-version.txt is missing from install directory'
[ -f release-arch.txt ] || fail 'release-arch.txt is missing from install directory'
[ -f CHECKSUMS.sha256 ] || fail 'CHECKSUMS.sha256 is missing from install directory'
[ -f images/backend.tar ] || fail 'backend image archive is missing from install directory'
[ -f images/frontend.tar ] || fail 'frontend image archive is missing from install directory'

require_command sha256sum
require_command tar
require_command docker

# Verify the matching release kit itself before using bundled images/scripts for recovery.
sha256sum -c CHECKSUMS.sha256

backup_dir=$(CDPATH= cd -- "$(dirname "$BACKUP_INPUT")" && pwd -P)
backup_base=$(basename "$BACKUP_INPUT")
sidecar="$BACKUP_INPUT.sha256"
[ "$(wc -l < "$sidecar" | tr -d ' ')" = 1 ] || fail 'backup .sha256 sidecar must contain exactly one entry'
sidecar_name=$(awk 'NR == 1 { print $2 }' "$sidecar")
[ "$sidecar_name" = "$backup_base" ] || fail 'backup .sha256 sidecar references an unexpected file'
(
  cd "$backup_dir"
  sha256sum -c "$backup_base.sha256"
)

tmp=$(mktemp -d)
cleanup() {
  status=$?
  rm -rf "$tmp"
  trap - 0 HUP INT TERM
  exit "$status"
}
trap cleanup 0 HUP INT TERM

if ! tar -tzf "$BACKUP_INPUT" > "$tmp/outer-members.txt"; then
  fail 'backup archive is not a valid gzip tar archive'
fi
members=$(LC_ALL=C sort "$tmp/outer-members.txt")
expected=$(printf '%s\n' \
  './' \
  './.env' \
  './CHECKSUMS.sha256' \
  './backup-metadata.txt' \
  './portal-data.tar.gz' | LC_ALL=C sort)
[ "$members" = "$expected" ] || fail 'backup archive layout is not the expected strict allowlist'
if ! tar -tvzf "$BACKUP_INPUT" > "$tmp/outer-listing.txt"; then
  fail 'cannot inspect backup archive member types'
fi
awk '{
  t=substr($1,1,1)
  if (t != "-" && t != "d") exit 1
}' "$tmp/outer-listing.txt" || fail 'backup outer archive contains unsupported member type'

tar -xzf "$BACKUP_INPUT" -C "$tmp"
for member in .env CHECKSUMS.sha256 backup-metadata.txt portal-data.tar.gz; do
  [ -f "$tmp/$member" ] && [ ! -L "$tmp/$member" ] || \
    fail "invalid backup member: $member"
done

[ "$(wc -l < "$tmp/CHECKSUMS.sha256" | tr -d ' ')" = 3 ] || \
  fail 'backup checksum manifest must contain exactly three entries'
checksum_names=$(awk '{ print $2 }' "$tmp/CHECKSUMS.sha256" | LC_ALL=C sort)
expected_checksum_names=$(printf '%s\n' .env backup-metadata.txt portal-data.tar.gz | LC_ALL=C sort)
[ "$checksum_names" = "$expected_checksum_names" ] || \
  fail 'backup checksum manifest references unexpected files'
(
  cd "$tmp"
  sha256sum -c CHECKSUMS.sha256
)

product=$(sed -n 's/^product=//p' "$tmp/backup-metadata.txt")
backup_version=$(sed -n 's/^version=//p' "$tmp/backup-metadata.txt")
volume_name=$(sed -n 's/^volume=//p' "$tmp/backup-metadata.txt")
backup_env_version=$(sed -n 's/^PORTAL_VERSION=//p' "$tmp/.env" | head -n 1)
backup_contour=$(sed -n 's/^PORTAL_CONTOUR=//p' "$tmp/.env" | head -n 1)
kit_version=$(cat release-version.txt)

[ "$product" = harbor-transfer-portal ] || fail 'backup product mismatch'
[ -n "$backup_version" ] || fail 'backup version is missing'
[ "$backup_env_version" = "$backup_version" ] || \
  fail "backup metadata/.env version mismatch: metadata=$backup_version env=$backup_env_version"
[ "$backup_version" = "$kit_version" ] || \
  fail "restore requires matching kit version: kit=$kit_version backup=$backup_version"
[ "$volume_name" = harbor-transfer-portal_portal-data ] || fail 'backup volume identity mismatch'
case "$backup_contour" in
  SOURCE|TARGET) ;;
  *) fail 'backup contour is invalid' ;;
esac

if [ -e .env ] || [ -L .env ]; then
  [ -f .env ] && [ ! -L .env ] || fail 'existing .env must be a regular non-symlink file'
  current_version=$(sed -n 's/^PORTAL_VERSION=//p' .env | head -n 1)
  current_contour=$(sed -n 's/^PORTAL_CONTOUR=//p' .env | head -n 1)
  [ "$current_version" = "$backup_version" ] || \
    fail "restore refuses version change: current=$current_version backup=$backup_version"
  [ "$current_contour" = "$backup_contour" ] || \
    fail "restore refuses contour change: current=$current_contour backup=$backup_contour"
fi

if ! tar -tzf "$tmp/portal-data.tar.gz" > "$tmp/data-members.txt"; then
  fail 'persistent-data archive is not a valid gzip tar archive'
fi
while IFS= read -r member; do
  case "$member" in
    /*|..|../*|*/../*|*/..) fail 'persistent-data archive contains unsafe path' ;;
  esac
done < "$tmp/data-members.txt"
if ! tar -tvzf "$tmp/portal-data.tar.gz" > "$tmp/data-listing.txt"; then
  fail 'cannot inspect persistent-data archive member types'
fi
awk '{
  t=substr($1,1,1)
  if (t != "-" && t != "d") exit 1
}' "$tmp/data-listing.txt" || fail 'persistent-data archive contains unsupported member type'

release_arch=$(cat release-arch.txt)
host_arch=$(normalize_arch "$(uname -m)") || fail "unsupported host architecture: $(uname -m)"
[ "$host_arch" = "$release_arch" ] || \
  fail "release architecture $release_arch does not match host $host_arch"

docker info >/dev/null 2>&1 || fail 'Docker Engine is not available'
docker compose version >/dev/null 2>&1 || fail 'Docker Compose v2 is not available'

printf 'Loading matching release images for recovery...\n'
docker load -i images/backend.tar
docker load -i images/frontend.tar
backend_image="harbor-transfer-portal-backend:$backup_version"
frontend_image="harbor-transfer-portal-frontend:$backup_version"
for image in "$backend_image" "$frontend_image"; do
  docker image inspect "$image" >/dev/null 2>&1 || \
    fail "expected local image missing after docker load: $image"
  image_arch=$(docker image inspect --format '{{.Architecture}}' "$image")
  [ "$image_arch" = "$release_arch" ] || \
    fail "local image architecture mismatch for $image: $image_arch"
done

umask 077
cp "$tmp/.env" "$INSTALL_DIR/.env.restore.$$"
chmod 0600 "$INSTALL_DIR/.env.restore.$$"
mv "$INSTALL_DIR/.env.restore.$$" "$INSTALL_DIR/.env"

printf 'Stopping existing Portal containers before restore...\n'
docker compose --env-file "$INSTALL_DIR/.env" -f "$INSTALL_DIR/compose.yaml" \
  down --remove-orphans

if ! docker volume inspect "$volume_name" >/dev/null 2>&1; then
  docker volume create "$volume_name" >/dev/null
fi

printf 'Replacing persistent data from verified backup...\n'
docker run --rm --pull never --network none \
  -v "$volume_name:/app/data" \
  --entrypoint sh \
  "$backend_image" \
  -c 'find /app/data -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +'

docker run --rm --pull never --network none \
  -v "$volume_name:/app/data" \
  -v "$tmp/portal-data.tar.gz:/backup/portal-data.tar.gz:ro" \
  --entrypoint tar \
  "$backend_image" \
  -C /app/data -xzf /backup/portal-data.tar.gz

docker run --rm --pull never --network none \
  -v "$volume_name:/app/data" \
  --entrypoint chown \
  "$backend_image" \
  -R 10001:10001 /app/data

timeout=${PORTAL_INSTALL_TIMEOUT_SECONDS:-180}
case "$timeout" in
  ''|*[!0-9]*) fail 'PORTAL_INSTALL_TIMEOUT_SECONDS must be an integer' ;;
esac

docker compose --env-file "$INSTALL_DIR/.env" -f "$INSTALL_DIR/compose.yaml" \
  up -d --no-build --pull never --wait --wait-timeout "$timeout"

printf 'Restore completed for %s version %s.\n' "$backup_contour" "$backup_version"
printf 'Use the matching version for recovery first; perform a normal upgrade only after health verification.\n'
