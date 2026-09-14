#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd -P)
CANONICAL_VERSION=$(sed -n 's/^__version__ = "\([A-Za-z0-9._-][A-Za-z0-9._-]*\)"$/\1/p' "$ROOT/backend/app/__init__.py")
VERSION=${1:-$CANONICAL_VERSION}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 2
}

[ -n "$CANONICAL_VERSION" ] || fail 'cannot determine canonical version from backend/app/__init__.py'
[ "$VERSION" = "$CANONICAL_VERSION" ] \
  || fail "requested release version $VERSION does not match canonical $CANONICAL_VERSION"
command -v docker >/dev/null 2>&1 || fail 'docker is required'
docker info >/dev/null 2>&1 || fail 'Docker Engine is unavailable'

if VCS_REF=$(git -C "$ROOT" rev-parse --verify HEAD 2>/dev/null); then
  :
else
  VCS_REF=unknown
fi

BACKEND_IMAGE="harbor-transfer-portal-backend:$VERSION"
FRONTEND_IMAGE="harbor-transfer-portal-frontend:$VERSION"

cd "$ROOT"
docker build \
  --build-arg "RELEASE_VERSION=$VERSION" \
  --build-arg "VCS_REF=$VCS_REF" \
  -f backend/Dockerfile \
  -t "$BACKEND_IMAGE" \
  .
docker build \
  --build-arg "RELEASE_VERSION=$VERSION" \
  --build-arg "VCS_REF=$VCS_REF" \
  -f frontend/Dockerfile \
  -t "$FRONTEND_IMAGE" \
  .

for image in "$BACKEND_IMAGE" "$FRONTEND_IMAGE"; do
  image_version=$(docker image inspect \
    --format '{{ index .Config.Labels "org.opencontainers.image.version" }}' "$image")
  [ "$image_version" = "$VERSION" ] \
    || fail "image release label mismatch for $image: $image_version"
done

printf 'Release images built for %s:\n  %s\n  %s\n' "$VERSION" "$BACKEND_IMAGE" "$FRONTEND_IMAGE"
