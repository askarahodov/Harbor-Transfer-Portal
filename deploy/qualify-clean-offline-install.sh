#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd -P)
VERSION=${1:-0.0.0-clean-host}
TMP=$(mktemp -d)
DIST="$TMP/dist"
ARCHIVE="$DIST/harbor-transfer-portal-v${VERSION}-offline-install.tar.gz"
BACKEND_IMAGE="harbor-transfer-portal-backend:$VERSION"
FRONTEND_IMAGE="harbor-transfer-portal-frontend:$VERSION"
VOLUME_NAME=harbor-transfer-portal_portal-data
NETWORK_NAME=harbor-transfer-portal_default

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

cleanup() {
  status=$?
  set +e
  for kit in "$TMP/source/harbor-transfer-portal-v${VERSION}-offline-install" \
             "$TMP/target/harbor-transfer-portal-v${VERSION}-offline-install"; do
    if [ -f "$kit/compose.yaml" ] && [ -f "$kit/.env" ]; then
      docker compose --env-file "$kit/.env" -f "$kit/compose.yaml" \
        down --remove-orphans --volumes >/dev/null 2>&1
    fi
  done
  docker volume rm -f "$VOLUME_NAME" >/dev/null 2>&1
  docker network rm "$NETWORK_NAME" >/dev/null 2>&1
  docker image rm -f "$BACKEND_IMAGE" "$FRONTEND_IMAGE" >/dev/null 2>&1
  rm -rf "$TMP"
  trap - EXIT HUP INT TERM
  exit "$status"
}
trap cleanup EXIT HUP INT TERM

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"
}

for command in docker tar sha256sum stat; do
  require_command "$command"
done

docker info >/dev/null 2>&1 || fail 'Docker Engine is not available'
docker compose version >/dev/null 2>&1 || fail 'Docker Compose v2 is not available'

case "$VERSION" in
  *[!A-Za-z0-9._-]*|'') fail 'VERSION contains unsupported characters' ;;
esac

assert_clean_runtime() {
  if docker volume inspect "$VOLUME_NAME" >/dev/null 2>&1; then
    fail "qualification volume already exists: $VOLUME_NAME"
  fi
  if docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
    fail "qualification network already exists: $NETWORK_NAME"
  fi
  if docker ps -a --format '{{.Names}}' | grep -E '^harbor-transfer-portal-(backend|frontend)-1$' >/dev/null; then
    fail 'qualification containers already exist before install'
  fi
}

assert_release_images_absent() {
  if docker image inspect "$BACKEND_IMAGE" >/dev/null 2>&1; then
    fail "release backend image unexpectedly exists before install: $BACKEND_IMAGE"
  fi
  if docker image inspect "$FRONTEND_IMAGE" >/dev/null 2>&1; then
    fail "release frontend image unexpectedly exists before install: $FRONTEND_IMAGE"
  fi
}

wait_runtime() {
  kit=$1
  attempts=0
  while [ "$attempts" -lt 30 ]; do
    if docker compose --env-file "$kit/.env" -f "$kit/compose.yaml" \
      exec -T frontend wget -q -O /dev/null http://127.0.0.1/healthz >/dev/null 2>&1 \
      && docker compose --env-file "$kit/.env" -f "$kit/compose.yaml" \
        exec -T backend python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2).read()" >/dev/null 2>&1; then
      return 0
    fi
    attempts=$((attempts + 1))
    sleep 2
  done
  fail 'offline-installed runtime did not become healthy'
}

verify_install() {
  contour=$1
  kit=$2

  [ -f "$kit/.env" ] && [ ! -L "$kit/.env" ] || fail 'installer did not create a regular .env'
  mode=$(stat -c '%a' "$kit/.env")
  [ "$mode" = 600 ] || fail "installer created .env with unexpected mode: $mode"
  grep -Fx "PORTAL_CONTOUR=$contour" "$kit/.env" >/dev/null || fail "installed contour is not $contour"
  grep -Fx "PORTAL_VERSION=$VERSION" "$kit/.env" >/dev/null || fail 'installed version does not match release kit'
  grep -Eq '^JWT_SECRET=[0-9a-f]{64,}$' "$kit/.env" || fail 'installer did not generate a strong JWT secret'

  docker image inspect "$BACKEND_IMAGE" >/dev/null 2>&1 || fail 'bundled backend image was not loaded'
  docker image inspect "$FRONTEND_IMAGE" >/dev/null 2>&1 || fail 'bundled frontend image was not loaded'

  wait_runtime "$kit"
  docker compose --env-file "$kit/.env" -f "$kit/compose.yaml" \
    exec -T frontend wget -q -O - http://127.0.0.1/api/health \
    | grep -F '"status":"ok"' >/dev/null
  docker compose --env-file "$kit/.env" -f "$kit/compose.yaml" \
    exec -T frontend wget -q -O - http://127.0.0.1/runtime-config.js \
    | grep -F "contour: '$contour'" >/dev/null
  docker compose --env-file "$kit/.env" -f "$kit/compose.yaml" \
    exec -T backend python -m alembic -c /app/alembic.ini current --check-heads >/dev/null
  docker compose --env-file "$kit/.env" -f "$kit/compose.yaml" \
    exec -T backend sh -c "skopeo --version | grep -F '1.9.3' >/dev/null"
  docker compose --env-file "$kit/.env" -f "$kit/compose.yaml" \
    exec -T backend sh -c "helm version --short | grep -F 'v3.22.0' >/dev/null"
}

qualify_contour() {
  contour=$1
  destination=$2

  assert_clean_runtime
  assert_release_images_absent
  mkdir -p "$destination"
  tar -xzf "$ARCHIVE" -C "$destination"
  kit="$destination/harbor-transfer-portal-v${VERSION}-offline-install"
  [ -x "$kit/install.sh" ] || fail 'release install.sh is missing or not executable'

  printf 'Qualifying clean offline install for %s...\n' "$contour"
  (
    cd "$kit"
    PORTAL_CONTOUR="$contour" sh ./install.sh
  )
  verify_install "$contour" "$kit"

  env_before=$(sha256sum "$kit/.env" | awk '{print $1}')
  docker compose --env-file "$kit/.env" -f "$kit/compose.yaml" \
    exec -T backend sh -c "printf 'clean-install-persistent\\n' > /app/data/.clean-install-qualification"

  # Re-running the documented command for the same version must preserve config and state.
  (
    cd "$kit"
    PORTAL_CONTOUR="$contour" sh ./install.sh >/dev/null
  )
  env_after=$(sha256sum "$kit/.env" | awk '{print $1}')
  [ "$env_before" = "$env_after" ] || fail 'matching-version installer rerun changed .env'
  docker compose --env-file "$kit/.env" -f "$kit/compose.yaml" \
    exec -T backend test -f /app/data/.clean-install-qualification

  docker compose --env-file "$kit/.env" -f "$kit/compose.yaml" restart >/dev/null
  wait_runtime "$kit"
  docker compose --env-file "$kit/.env" -f "$kit/compose.yaml" \
    exec -T backend test -f /app/data/.clean-install-qualification

  # CI qualification intentionally tears down this isolated test installation completely
  # before exercising the same immutable archive in the opposite contour.
  docker compose --env-file "$kit/.env" -f "$kit/compose.yaml" \
    down --remove-orphans --volumes >/dev/null
  docker image rm -f "$BACKEND_IMAGE" "$FRONTEND_IMAGE" >/dev/null
  assert_clean_runtime
  assert_release_images_absent
}

cd "$ROOT"
assert_clean_runtime

printf 'Build phase: creating release images and immutable offline kit...\n'
docker build -f backend/Dockerfile -t "$BACKEND_IMAGE" .
docker build -f frontend/Dockerfile -t "$FRONTEND_IMAGE" .
mkdir -p "$DIST"
sh deploy/build-offline-kit.sh "$VERSION" "$DIST"
[ -f "$ARCHIVE" ] || fail 'offline release archive was not created'
[ -f "$ARCHIVE.sha256" ] || fail 'offline release archive checksum was not created'
(
  cd "$DIST"
  sha256sum -c "$(basename "$ARCHIVE").sha256" >/dev/null
)

# Install phase boundary: the release-tagged images are deliberately absent.
docker image rm -f "$BACKEND_IMAGE" "$FRONTEND_IMAGE" >/dev/null
assert_release_images_absent

qualify_contour SOURCE "$TMP/source"
qualify_contour TARGET "$TMP/target"

printf 'Clean-host offline install qualification passed for SOURCE and TARGET using the same archive.\n'
