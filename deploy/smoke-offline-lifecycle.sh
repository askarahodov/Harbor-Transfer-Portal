#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT HUP INT TERM

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

FAKE_BIN="$TMP/bin"
FAKE_LOG="$TMP/docker.log"
mkdir -p "$FAKE_BIN"
: > "$FAKE_LOG"

cat > "$FAKE_BIN/docker" <<'EOF'
#!/bin/sh
set -eu
: "${FAKE_DOCKER_LOG:?}"

log_command() {
  printf '%s' "$1" >> "$FAKE_DOCKER_LOG"
  shift
  for arg in "$@"; do
    printf ' %s' "$arg" >> "$FAKE_DOCKER_LOG"
  done
  printf '\n' >> "$FAKE_DOCKER_LOG"
}

case "${1:-}" in
  image)
    [ "${2:-}" = inspect ] || exit 2
    case " $* " in
      *' --format '*) printf '%s\n' amd64 ;;
    esac
    ;;
  volume)
    [ "${2:-}" = inspect ] || exit 2
    ;;
  save)
    [ "${2:-}" = -o ] || exit 2
    printf 'fake docker image: %s\n' "${4:-unknown}" > "$3"
    ;;
  info)
    ;;
  load)
    [ "${2:-}" = -i ] || exit 2
    [ -f "${3:-}" ] || exit 2
    log_command load "$3"
    ;;
  run)
    log_command "$@"
    printf 'fake persistent volume snapshot\n'
    ;;
  compose)
    if [ "${2:-}" = version ]; then
      exit 0
    fi
    log_command "$@"
    command_name=
    for arg in "$@"; do
      case "$arg" in
        ps|pause|unpause|up|down)
          if [ -z "$command_name" ]; then
            command_name=$arg
          fi
          ;;
      esac
    done
    case "$command_name" in
      ps)
        if [ "${FAKE_DOCKER_RUNNING:-1}" = 1 ]; then
          printf '%s\n' fake-running-container
        fi
        ;;
      up)
        [ "${FAKE_DOCKER_FAIL_UP:-0}" != 1 ] || exit 17
        ;;
    esac
    ;;
  *)
    printf 'unexpected fake docker invocation: %s\n' "$*" >&2
    exit 2
    ;;
esac
EOF
chmod 0755 "$FAKE_BIN/docker"

export PATH="$FAKE_BIN:$PATH"
export FAKE_DOCKER_LOG="$FAKE_LOG"

NEW_VERSION=0.0.2-lifecycle
OLD_VERSION=0.0.1-old
DIST="$TMP/dist"
sh "$ROOT/deploy/build-offline-kit.sh" "$NEW_VERSION" "$DIST"
ARCHIVE="$DIST/harbor-transfer-portal-v${NEW_VERSION}-offline-install.tar.gz"
[ -f "$ARCHIVE" ] || fail 'lifecycle release archive was not created'

extract_kit() {
  destination=$1
  mkdir -p "$destination"
  tar -xzf "$ARCHIVE" -C "$destination"
  printf '%s\n' "$destination/harbor-transfer-portal-v${NEW_VERSION}-offline-install"
}

KIT_FAIL=$(extract_kit "$TMP/fail")
KIT_OK=$(extract_kit "$TMP/ok")

for script in backup.sh upgrade.sh uninstall.sh; do
  [ -x "$KIT_OK/$script" ] || fail "lifecycle script is missing or not executable: $script"
  grep -F "  $script" "$KIT_OK/CHECKSUMS.sha256" >/dev/null || \
    fail "lifecycle script is not covered by payload checksums: $script"
done

PREVIOUS="$TMP/previous-install"
mkdir -p "$PREVIOUS"
cp "$KIT_OK/compose.yaml" "$PREVIOUS/compose.yaml"
cat > "$PREVIOUS/.env" <<EOF
PORTAL_CONTOUR=TARGET
PORTAL_HTTP_PORT=18080
PORTAL_VERSION=$OLD_VERSION
JWT_SECRET=keep-this-secret
CUSTOM_SETTING=preserve-me
EOF
chmod 0600 "$PREVIOUS/.env"

# Failed upgrade must still create the backup first and restore old configuration.
: > "$FAKE_LOG"
if PORTAL_BACKUP_DIR="$TMP/backups-fail" FAKE_DOCKER_FAIL_UP=1 \
  sh "$KIT_FAIL/upgrade.sh" "$PREVIOUS" >/dev/null 2>&1; then
  fail 'upgrade unexpectedly succeeded when Compose startup failed'
fi
grep -Fx "PORTAL_VERSION=$OLD_VERSION" "$KIT_FAIL/.env" >/dev/null || \
  fail 'failed upgrade did not restore previous PORTAL_VERSION'
grep -Fx 'CUSTOM_SETTING=preserve-me' "$KIT_FAIL/.env" >/dev/null || \
  fail 'failed upgrade did not preserve unrelated configuration'

backup_fail=$(find "$TMP/backups-fail" -maxdepth 1 -type f -name '*.tar.gz' | head -n 1)
[ -n "$backup_fail" ] && [ -f "$backup_fail" ] || fail 'failed upgrade did not create pre-upgrade backup'
[ -f "$backup_fail.sha256" ] || fail 'backup archive checksum is missing'
(
  cd "$(dirname "$backup_fail")"
  sha256sum -c "$(basename "$backup_fail").sha256" >/dev/null
)
BACKUP_EXTRACT="$TMP/backup-extract"
mkdir -p "$BACKUP_EXTRACT"
tar -xzf "$backup_fail" -C "$BACKUP_EXTRACT"
(
  cd "$BACKUP_EXTRACT"
  sha256sum -c CHECKSUMS.sha256 >/dev/null
)
grep -Fx "PORTAL_VERSION=$OLD_VERSION" "$BACKUP_EXTRACT/.env" >/dev/null || \
  fail 'backup does not contain the pre-upgrade configuration'
grep -F 'fake persistent volume snapshot' "$BACKUP_EXTRACT/portal-data.tar.gz" >/dev/null || \
  fail 'backup does not contain the persistent-volume snapshot stream'

backup_line=$(grep -n '^run ' "$FAKE_LOG" | head -n 1 | cut -d: -f1)
load_line=$(grep -n '^load ' "$FAKE_LOG" | head -n 1 | cut -d: -f1)
[ -n "$backup_line" ] && [ -n "$load_line" ] && [ "$backup_line" -lt "$load_line" ] || \
  fail 'new images were loaded before mandatory pre-upgrade backup'
grep -F 'run --rm --pull never --network none --entrypoint tar' "$FAKE_LOG" >/dev/null || \
  fail 'backup helper must use local image with explicit no-pull/no-network boundary'
grep -F "harbor-transfer-portal-backend:$OLD_VERSION" "$FAKE_LOG" >/dev/null || \
  fail 'backup did not use the currently configured backend image'
grep -F 'compose --env-file' "$FAKE_LOG" | grep -F ' up -d --no-build --pull never --wait --wait-timeout 180' >/dev/null || \
  fail 'upgrade must start Compose with explicit no-build/no-pull semantics'

# Successful upgrade must preserve every setting except PORTAL_VERSION.
: > "$FAKE_LOG"
PORTAL_BACKUP_DIR="$TMP/backups-ok" sh "$KIT_OK/upgrade.sh" "$PREVIOUS" >/dev/null
grep -Fx "PORTAL_VERSION=$NEW_VERSION" "$KIT_OK/.env" >/dev/null || \
  fail 'successful upgrade did not set the new version'
grep -Fx 'JWT_SECRET=keep-this-secret' "$KIT_OK/.env" >/dev/null || \
  fail 'successful upgrade replaced JWT secret'
grep -Fx 'CUSTOM_SETTING=preserve-me' "$KIT_OK/.env" >/dev/null || \
  fail 'successful upgrade dropped unrelated configuration'
grep -Fx "PORTAL_VERSION=$OLD_VERSION" "$PREVIOUS/.env" >/dev/null || \
  fail 'separate previous install directory was mutated'
[ "$(grep -c '^load ' "$FAKE_LOG")" -eq 2 ] || fail 'upgrade must load exactly two bundled images'

# Default uninstall is deliberately non-destructive.
: > "$FAKE_LOG"
sh "$KIT_OK/uninstall.sh" "$KIT_OK" >/dev/null
grep -F ' down --remove-orphans' "$FAKE_LOG" >/dev/null || fail 'default uninstall did not call Compose down'
if grep -F -- '--volumes' "$FAKE_LOG" >/dev/null; then
  fail 'default uninstall attempted to remove persistent volumes'
fi
[ -f "$KIT_OK/.env" ] || fail 'default uninstall removed .env'

# Purge must fail before destructive Docker invocation unless explicitly confirmed.
: > "$FAKE_LOG"
if sh "$KIT_OK/uninstall.sh" "$KIT_OK" --purge-data >/dev/null 2>&1; then
  fail 'purge without explicit confirmation was accepted'
fi
[ ! -s "$FAKE_LOG" ] || fail 'unconfirmed purge reached a destructive Docker invocation'

: > "$FAKE_LOG"
PORTAL_CONFIRM_PURGE=DELETE_PORTAL_DATA sh "$KIT_OK/uninstall.sh" "$KIT_OK" --purge-data >/dev/null
grep -F ' down --remove-orphans --volumes' "$FAKE_LOG" >/dev/null || \
  fail 'confirmed purge did not request persistent volume removal'
[ -f "$KIT_OK/.env" ] || fail 'confirmed data purge must still leave .env for manual handling'

printf 'Offline lifecycle smoke passed.\n'
