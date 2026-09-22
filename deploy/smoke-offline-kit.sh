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
    last_arg=
    for arg in "$@"; do
      last_arg=$arg
    done
    printf 'image %s\n' "$*" >> "$FAKE_DOCKER_LOG"
    if [ -n "${FAKE_DOCKER_MISSING_IMAGE:-}" ] && [ "$last_arg" = "$FAKE_DOCKER_MISSING_IMAGE" ]; then
      exit 1
    fi
    if [ "${3:-}" = --format ]; then
      case "${4:-}" in
        *Architecture*) printf '%s\n' amd64 ;;
        *org.opencontainers.image.version*)
          if [ -n "${FAKE_DOCKER_LABEL_VERSION:-}" ]; then
            printf '%s\n' "$FAKE_DOCKER_LABEL_VERSION"
          else
            printf '%s\n' "${last_arg##*:}"
          fi
          ;;
        *org.opencontainers.image.revision*)
          printf '%s\n' "${FAKE_DOCKER_LABEL_REVISION:-unknown}"
          ;;
        *) exit 2 ;;
      esac
    fi
    ;;
  save)
    [ "${2:-}" = -o ] || exit 2
    printf 'save %s\n' "$*" >> "$FAKE_DOCKER_LOG"
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
SOURCE_REVISION=$(git -C "$ROOT" rev-parse --verify HEAD 2>/dev/null || printf 'unknown')
export FAKE_DOCKER_LABEL_REVISION="$SOURCE_REVISION"

VERSION=1.0.0
DIST="$TMP/dist"

# A release archive must never be built for a version that differs from the
# committed product version. This must fail before any Docker inspection/save.
: > "$FAKE_LOG"
if sh "$ROOT/deploy/build-offline-kit.sh" 0.0.0-smoke "$TMP/version-mismatch" >/dev/null 2>&1; then
  fail 'non-canonical release version was accepted'
fi
[ ! -s "$FAKE_LOG" ] || fail 'version mismatch reached Docker instead of failing closed'

# Even correctly tagged images are rejected when their OCI release label disagrees.
: > "$FAKE_LOG"
if FAKE_DOCKER_LABEL_VERSION=0.9.0 \
  sh "$ROOT/deploy/build-offline-kit.sh" "$VERSION" "$TMP/label-mismatch" >/dev/null 2>&1; then
  fail 'image with mismatched OCI version label was accepted'
fi
if grep -q '^save ' "$FAKE_LOG"; then
  fail 'image label mismatch reached docker save instead of failing closed'
fi

# When a Git revision is available, stale release images from another commit
# must not be packaged under the current release manifest identity.
if [ "$SOURCE_REVISION" != unknown ]; then
  : > "$FAKE_LOG"
  if FAKE_DOCKER_LABEL_REVISION=deadbeef \
    sh "$ROOT/deploy/build-offline-kit.sh" "$VERSION" "$TMP/revision-mismatch" >/dev/null 2>&1; then
    fail 'image with mismatched OCI revision label was accepted'
  fi
  if grep -q '^save ' "$FAKE_LOG"; then
    fail 'image revision mismatch reached docker save instead of failing closed'
  fi
fi

: > "$FAKE_LOG"
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
  CHANGELOG.md \
  .env.example \
  compose.yaml \
  install.sh \
  backup.sh \
  restore.sh \
  upgrade.sh \
  uninstall.sh \
  release-version.txt \
  release-arch.txt \
  release-manifest.json \
  docs/release-notes-v1.0.0.md \
  docs/user-guide.md \
  docs/browser-transport.md \
  images/backend.tar \
  images/frontend.tar; do
  [ -f "$KIT/$required" ] || fail "missing release payload file: $required"
done
for executable in install.sh backup.sh restore.sh upgrade.sh uninstall.sh; do
  [ -x "$KIT/$executable" ] || fail "release script is not executable: $executable"
done
[ ! -e "$KIT/.env" ] || fail 'release payload must not contain .env'
if grep -Eq '^[[:space:]]+build:' "$KIT/compose.yaml"; then
  fail 'offline compose must not contain build sections'
fi
grep -Fx 'name: harbor-transfer-portal' "$KIT/compose.yaml" >/dev/null || \
  fail 'offline compose must use a stable project name'
[ "$(grep -c '^[[:space:]]*pull_policy: never$' "$KIT/compose.yaml")" -eq 2 ] || \
  fail 'offline compose must disable pulling for both services'
if grep -F 'network_mode: host' "$KIT/compose.yaml" >/dev/null; then
  fail 'offline compose must not depend on host network mode'
fi
grep -F '"${PORTAL_HTTP_BIND:-127.0.0.1}:${PORTAL_HTTP_PORT:-8080}:8080"' "$KIT/compose.yaml" >/dev/null || \
  fail 'offline compose must publish only the frontend browser port'
grep -Eq '^[0-9a-f]{64}  docs/user-guide\.md$' "$KIT/CHECKSUMS.sha256" || \
  fail 'operator user guide must be covered by CHECKSUMS.sha256'
grep -Eq '^[0-9a-f]{64}  docs/browser-transport\.md$' "$KIT/CHECKSUMS.sha256" || \
  fail 'browser transport guide must be covered by CHECKSUMS.sha256'
(
  cd "$KIT"
  sha256sum -c CHECKSUMS.sha256
)

[ "$(cat "$KIT/release-version.txt")" = "$VERSION" ] || fail 'release-version.txt mismatch'
grep -F '"version": "1.0.0"' "$KIT/release-manifest.json" >/dev/null
grep -F '"architecture": "amd64"' "$KIT/release-manifest.json" >/dev/null
grep -F '"release_notes": "docs/release-notes-v1.0.0.md"' "$KIT/release-manifest.json" >/dev/null
if [ "$SOURCE_REVISION" != unknown ]; then
  grep -F "\"source_revision\": \"$SOURCE_REVISION\"" "$KIT/release-manifest.json" >/dev/null \
    || fail 'release manifest revision mismatch'
fi

: > "$FAKE_LOG"
(
  cd "$KIT"
  PORTAL_CONTOUR=TARGET sh ./install.sh
)
[ -f "$KIT/.env" ] || fail 'installer did not create .env'
grep -Fx 'PORTAL_VERSION=1.0.0' "$KIT/.env" >/dev/null
grep -Fx 'PORTAL_CONTOUR=TARGET' "$KIT/.env" >/dev/null
grep -Fx 'PORTAL_HTTP_BIND=127.0.0.1' "$KIT/.env" >/dev/null \
  || fail 'installer did not preserve loopback browser listener default'
grep -Fx 'PORTAL_BROWSER_SCHEME=http' "$KIT/.env" >/dev/null \
  || fail 'installer did not preserve explicit HTTP diagnostic scheme default'
grep -Eq '^JWT_SECRET=[0-9a-f]{64,}$' "$KIT/.env" || fail 'installer did not generate a strong JWT secret'
[ "$(grep -c '^load ' "$FAKE_LOG")" -eq 2 ] || fail 'installer must load exactly two images'
grep -F 'compose --env-file .env -f compose.yaml up -d --no-build --pull never --wait --wait-timeout 180' "$FAKE_LOG" >/dev/null || \
  fail 'installer must start Compose with explicit no-build/no-pull semantics'

env_before=$(sha256sum "$KIT/.env" | awk '{print $1}')
(
  cd "$KIT"
  PORTAL_CONTOUR=SOURCE sh ./install.sh
)
env_after=$(sha256sum "$KIT/.env" | awk '{print $1}')
[ "$env_before" = "$env_after" ] || fail 'rerun overwrote existing .env'

# Plain HTTP must never be published on a non-loopback interface. Reject this
# before image load or Compose startup so a bad edit fails closed immediately.
cp "$KIT/.env" "$KIT/.env.safe-browser-transport"
sed 's/^PORTAL_HTTP_BIND=.*/PORTAL_HTTP_BIND=0.0.0.0/' "$KIT/.env.safe-browser-transport" > "$KIT/.env"
: > "$FAKE_LOG"
if (
  cd "$KIT"
  sh ./install.sh >/dev/null 2>&1
); then
  fail 'non-loopback plain HTTP browser listener was accepted'
fi
[ ! -s "$FAKE_LOG" ] || fail 'unsafe browser transport reached Docker activity'
mv "$KIT/.env.safe-browser-transport" "$KIT/.env"

# Existing config must not be trusted through a symlink.
mv "$KIT/.env" "$KIT/.env.real"
ln -s .env.real "$KIT/.env"
: > "$FAKE_LOG"
if (
  cd "$KIT"
  sh ./install.sh >/dev/null 2>&1
); then
  fail 'symlinked .env was accepted'
fi
[ ! -s "$FAKE_LOG" ] || fail 'symlinked .env reached Docker image load/Compose'
rm "$KIT/.env"
mv "$KIT/.env.real" "$KIT/.env"

# docker load must create the exact local image refs expected by Compose. Missing/mistagged
# payload must fail before Compose gets a chance to use its normal registry behavior.
: > "$FAKE_LOG"
if (
  cd "$KIT"
  FAKE_DOCKER_MISSING_IMAGE="harbor-transfer-portal-backend:${VERSION}" sh ./install.sh >/dev/null 2>&1
); then
  fail 'missing expected local image was accepted'
fi
[ "$(grep -c '^load ' "$FAKE_LOG")" -eq 2 ] || fail 'missing-image case did not reach both docker loads'
if grep -q '^compose' "$FAKE_LOG"; then
  fail 'missing expected local image reached Compose instead of failing closed'
fi

printf 'tampered\n' >> "$KIT/images/backend.tar"
: > "$FAKE_LOG"
if (
  cd "$KIT"
  sh ./install.sh >/dev/null 2>&1
); then
  fail 'tampered payload was accepted'
fi
[ ! -s "$FAKE_LOG" ] || fail 'tampered payload reached Docker before checksum rejection'

sh "$ROOT/deploy/smoke-offline-lifecycle.sh"
printf 'Offline release kit smoke passed.\n'
