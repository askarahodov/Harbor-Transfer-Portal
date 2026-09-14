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
    last_arg=
    for arg in "$@"; do
      last_arg=$arg
    done
    if [ "${3:-}" = --format ]; then
      case "${4:-}" in
        *Architecture*) printf '%s\n' amd64 ;;
        *org.opencontainers.image.version*) printf '%s\n' "${last_arg##*:}" ;;
        *) exit 2 ;;
      esac
    fi
    ;;
  volume)
    case "${2:-}" in
      inspect) ;;
      create) printf '%s\n' "${3:-harbor-transfer-portal_portal-data}" ;;
      *) exit 2 ;;
    esac
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
    case " $* " in
      *' -czf - '*)
        data_tmp=$(mktemp -d)
        printf 'fake persistent volume snapshot\n' > "$data_tmp/marker.txt"
        tar -C "$data_tmp" -czf - .
        rm -rf "$data_tmp"
        ;;
    esac
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

NEW_VERSION=1.0.0
OLD_VERSION=0.9.0-old
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

for script in backup.sh restore.sh upgrade.sh uninstall.sh; do
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
tar -tzf "$BACKUP_EXTRACT/portal-data.tar.gz" | grep -F './marker.txt' >/dev/null || \
  fail 'backup does not contain the persistent-volume snapshot archive'

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

# Produce a matching-version recovery backup after the successful upgrade.
PORTAL_BACKUP_DIR="$TMP/backups-restore" sh "$KIT_OK/backup.sh" "$KIT_OK" >/dev/null
restore_backup=$(find "$TMP/backups-restore" -maxdepth 1 -type f -name '*.tar.gz' | head -n 1)
[ -n "$restore_backup" ] && [ -f "$restore_backup" ] || fail 'restore fixture backup was not created'

# Restore is destructive and must require confirmation before any Docker operation.
: > "$FAKE_LOG"
if sh "$KIT_OK/restore.sh" "$restore_backup" >/dev/null 2>&1; then
  fail 'restore without explicit confirmation was accepted'
fi
[ ! -s "$FAKE_LOG" ] || fail 'unconfirmed restore reached Docker'

# External backup tampering must be rejected before Docker mutation.
tampered_backup="$TMP/backups-restore/tampered.tar.gz"
cp "$restore_backup" "$tampered_backup"
(
  cd "$(dirname "$tampered_backup")"
  sha256sum "$(basename "$tampered_backup")" > "$(basename "$tampered_backup").sha256"
)
printf 'tamper\n' >> "$tampered_backup"
: > "$FAKE_LOG"
if sh "$KIT_OK/restore.sh" "$tampered_backup" --confirm-restore "$KIT_OK" >/dev/null 2>&1; then
  fail 'tampered backup was accepted'
fi
[ ! -s "$FAKE_LOG" ] || fail 'tampered backup reached Docker before checksum rejection'

# A checksum-valid backup with a traversal member in persistent data must also fail before Docker.
malicious_dir="$TMP/malicious-backup"
mkdir -p "$malicious_dir"
cp "$KIT_OK/.env" "$malicious_dir/.env"
cat > "$malicious_dir/backup-metadata.txt" <<EOF
product=harbor-transfer-portal
version=$NEW_VERSION
created_at_utc=20990101T000000Z
volume=harbor-transfer-portal_portal-data
EOF
python3 - "$malicious_dir/portal-data.tar.gz" <<'PY'
import io
import sys
import tarfile

payload = b"escape\n"
with tarfile.open(sys.argv[1], "w:gz") as archive:
    member = tarfile.TarInfo("../escape.txt")
    member.size = len(payload)
    archive.addfile(member, io.BytesIO(payload))
PY
(
  cd "$malicious_dir"
  sha256sum .env backup-metadata.txt portal-data.tar.gz > CHECKSUMS.sha256
)
malicious_backup="$TMP/backups-restore/malicious.tar.gz"
tar -C "$malicious_dir" -czf "$malicious_backup" .
(
  cd "$(dirname "$malicious_backup")"
  sha256sum "$(basename "$malicious_backup")" > "$(basename "$malicious_backup").sha256"
)
: > "$FAKE_LOG"
if sh "$KIT_OK/restore.sh" "$malicious_backup" --confirm-restore "$KIT_OK" >/dev/null 2>&1; then
  fail 'path-traversal backup was accepted'
fi
[ ! -s "$FAKE_LOG" ] || fail 'path-traversal backup reached Docker before archive validation'

# A valid matching-version restore must recover configuration and use only local/no-network runtime operations.
sed 's/^CUSTOM_SETTING=.*/CUSTOM_SETTING=changed-after-backup/' "$KIT_OK/.env" > "$KIT_OK/.env.changed"
chmod 0600 "$KIT_OK/.env.changed"
mv "$KIT_OK/.env.changed" "$KIT_OK/.env"
: > "$FAKE_LOG"
sh "$KIT_OK/restore.sh" "$restore_backup" --confirm-restore "$KIT_OK" >/dev/null
grep -Fx "PORTAL_VERSION=$NEW_VERSION" "$KIT_OK/.env" >/dev/null || \
  fail 'restore changed the release version'
grep -Fx 'PORTAL_CONTOUR=TARGET' "$KIT_OK/.env" >/dev/null || \
  fail 'restore changed the contour'
grep -Fx 'JWT_SECRET=keep-this-secret' "$KIT_OK/.env" >/dev/null || \
  fail 'restore did not recover JWT secret'
grep -Fx 'CUSTOM_SETTING=preserve-me' "$KIT_OK/.env" >/dev/null || \
  fail 'restore did not recover backed-up configuration'
[ "$(grep -c '^load ' "$FAKE_LOG")" -eq 2 ] || fail 'restore must load exactly two matching bundled images'
grep -F 'compose --env-file' "$FAKE_LOG" | grep -F ' down --remove-orphans' >/dev/null || \
  fail 'restore must stop existing Compose workload before replacing data'
[ "$(grep -c '^run .*--pull never --network none' "$FAKE_LOG")" -eq 3 ] || \
  fail 'restore data operations must use local backend image with no-pull/no-network boundary'
grep -F 'run --rm --pull never --network none' "$FAKE_LOG" | grep -F -- '--entrypoint tar' | grep -F ':/backup/portal-data.tar.gz:ro' >/dev/null || \
  fail 'restore must mount verified persistent data read-only into the local backend image'
grep -F 'compose --env-file' "$FAKE_LOG" | grep -F ' up -d --no-build --pull never --wait --wait-timeout 180' >/dev/null || \
  fail 'restore must start Compose with explicit no-build/no-pull semantics'

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
