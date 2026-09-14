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
case "${1:-}" in
  image)
    [ "${2:-}" = inspect ] || exit 2
    if [ "${3:-}" = --format ]; then
      printf '%s\n' amd64
    fi
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
    printf 'load %s\n' "$3" >> "$FAKE_DOCKER_LOG"
    ;;
  compose)
    if [ "${2:-}" = version ]; then
      exit 0
    fi
    printf 'compose' >> "$FAKE_DOCKER_LOG"
    shift
    for arg in "$@"; do
      printf ' %s' "$arg" >> "$FAKE_DOCKER_LOG"
    done
    printf '\n' >> "$FAKE_DOCKER_LOG"
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

VERSION=0.0.0-smoke
DIST="$TMP/dist"
sh "$ROOT/deploy/build-offline-kit.sh" "$VERSION" "$DIST"

ARCHIVE="$DIST/harbor-transfer-portal-v${VERSION}-offline-install.tar.gz"
[ -f "$ARCHIVE" ] || fail 'release archive was not created'
[ -f "$ARCHIVE.sha256" ] || fail 'external archive checksum was not created'
(
  cd "$DIST"
  sha256sum -c "$(basename "$ARCHIVE").sha256"
)

EXTRACT="$TMP/extract"
mkdir -p "$EXTRACT"
tar -xzf "$ARCHIVE" -C "$EXTRACT"
KIT="$EXTRACT/harbor-transfer-portal-v${VERSION}-offline-install"

for required in \
  CHECKSUMS.sha256 \
  README.md \
  .env.example \
  compose.yaml \
  install.sh \
  release-version.txt \
  release-arch.txt \
  release-manifest.json \
  images/backend.tar \
  images/frontend.tar; do
  [ -f "$KIT/$required" ] || fail "missing release payload file: $required"
done
[ ! -e "$KIT/.env" ] || fail 'release payload must not contain .env'
if grep -Eq '^[[:space:]]+build:' "$KIT/compose.yaml"; then
  fail 'offline compose must not contain build sections'
fi
(
  cd "$KIT"
  sha256sum -c CHECKSUMS.sha256
)

grep -F '"version": "0.0.0-smoke"' "$KIT/release-manifest.json" >/dev/null
grep -F '"architecture": "amd64"' "$KIT/release-manifest.json" >/dev/null

: > "$FAKE_LOG"
(
  cd "$KIT"
  PORTAL_CONTOUR=TARGET sh ./install.sh
)
[ -f "$KIT/.env" ] || fail 'installer did not create .env'
grep -Fx 'PORTAL_VERSION=0.0.0-smoke' "$KIT/.env" >/dev/null
grep -Fx 'PORTAL_CONTOUR=TARGET' "$KIT/.env" >/dev/null
grep -Eq '^JWT_SECRET=[0-9a-f]{64,}$' "$KIT/.env" || fail 'installer did not generate a strong JWT secret'
[ "$(grep -c '^load ' "$FAKE_LOG")" -eq 2 ] || fail 'installer must load exactly two images'
grep -F 'compose --env-file .env -f compose.yaml up -d --wait --wait-timeout 180' "$FAKE_LOG" >/dev/null

env_before=$(sha256sum "$KIT/.env" | awk '{print $1}')
(
  cd "$KIT"
  PORTAL_CONTOUR=SOURCE sh ./install.sh
)
env_after=$(sha256sum "$KIT/.env" | awk '{print $1}')
[ "$env_before" = "$env_after" ] || fail 'rerun overwrote existing .env'

printf 'tampered\n' >> "$KIT/images/backend.tar"
: > "$FAKE_LOG"
if (
  cd "$KIT"
  sh ./install.sh >/dev/null 2>&1
); then
  fail 'tampered payload was accepted'
fi
[ ! -s "$FAKE_LOG" ] || fail 'tampered payload reached Docker before checksum rejection'

printf 'Offline release kit smoke passed.\n'
