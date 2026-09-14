#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
CANONICAL_VERSION=$(sed -n 's/^__version__ = "\([A-Za-z0-9._-][A-Za-z0-9._-]*\)"$/\1/p' "$ROOT/backend/app/__init__.py")
VERSION=${1:-}
OUT_DIR=${2:-"$ROOT/dist"}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 2
}

[ -n "$CANONICAL_VERSION" ] || fail 'cannot determine canonical version from backend/app/__init__.py'
[ -n "$VERSION" ] || fail 'usage: deploy/build-offline-kit.sh VERSION [OUT_DIR]'
case "$VERSION" in
  *[!A-Za-z0-9._-]*|'') fail 'VERSION may contain only A-Z, a-z, 0-9, dot, underscore and dash' ;;
esac
[ "$VERSION" = "$CANONICAL_VERSION" ] \
  || fail "release VERSION $VERSION does not match canonical $CANONICAL_VERSION"

command -v docker >/dev/null 2>&1 || fail 'docker is required'
command -v sha256sum >/dev/null 2>&1 || fail 'sha256sum is required'
command -v tar >/dev/null 2>&1 || fail 'tar is required'

BACKEND_IMAGE="harbor-transfer-portal-backend:$VERSION"
FRONTEND_IMAGE="harbor-transfer-portal-frontend:$VERSION"

docker image inspect "$BACKEND_IMAGE" >/dev/null 2>&1 || fail "local image not found: $BACKEND_IMAGE"
docker image inspect "$FRONTEND_IMAGE" >/dev/null 2>&1 || fail "local image not found: $FRONTEND_IMAGE"

backend_arch=$(docker image inspect --format '{{.Architecture}}' "$BACKEND_IMAGE")
frontend_arch=$(docker image inspect --format '{{.Architecture}}' "$FRONTEND_IMAGE")
[ "$backend_arch" = "$frontend_arch" ] || fail "image architecture mismatch: backend=$backend_arch frontend=$frontend_arch"
case "$backend_arch" in
  amd64|arm64) ;;
  *) fail "unsupported release architecture: $backend_arch" ;;
esac

for image in "$BACKEND_IMAGE" "$FRONTEND_IMAGE"; do
  image_version=$(docker image inspect \
    --format '{{ index .Config.Labels "org.opencontainers.image.version" }}' "$image")
  [ "$image_version" = "$VERSION" ] \
    || fail "image release label mismatch for $image: expected $VERSION, got $image_version"
done

if source_revision=$(git -C "$ROOT" rev-parse --verify HEAD 2>/dev/null); then
  :
else
  source_revision=unknown
fi

helm_version=$(awk -F= '/^ARG HELM_VERSION=/{print $2; exit}' "$ROOT/backend/Dockerfile")
skopeo_package=$(awk -F= '/^ARG SKOPEO_DEBIAN_VERSION=/{print $2; exit}' "$ROOT/backend/Dockerfile")
[ -n "$helm_version" ] || fail 'cannot determine HELM_VERSION from backend/Dockerfile'
[ -n "$skopeo_package" ] || fail 'cannot determine SKOPEO_DEBIAN_VERSION from backend/Dockerfile'
[ -f "$ROOT/CHANGELOG.md" ] || fail 'CHANGELOG.md is required for a release kit'
[ -f "$ROOT/docs/release-notes-v1.0.0.md" ] || fail 'v1.0.0 release notes are required for a release kit'

PACKAGE_NAME="harbor-transfer-portal-v${VERSION}-offline-install"
mkdir -p "$OUT_DIR"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT HUP INT TERM
STAGE="$TMP/$PACKAGE_NAME"
mkdir -p "$STAGE/images" "$STAGE/docs"

printf 'Saving %s...\n' "$BACKEND_IMAGE"
docker save -o "$STAGE/images/backend.tar" "$BACKEND_IMAGE"
printf 'Saving %s...\n' "$FRONTEND_IMAGE"
docker save -o "$STAGE/images/frontend.tar" "$FRONTEND_IMAGE"

cp "$ROOT/deploy/offline/compose.yaml" "$STAGE/compose.yaml"
for script in install.sh backup.sh restore.sh upgrade.sh uninstall.sh; do
  cp "$ROOT/deploy/offline/$script" "$STAGE/$script"
  chmod 0755 "$STAGE/$script"
done
cp "$ROOT/deploy/offline/README.md" "$STAGE/README.md"
cp "$ROOT/.env.example" "$STAGE/.env.example"
cp "$ROOT/CHANGELOG.md" "$STAGE/CHANGELOG.md"
cp "$ROOT/docs/release-notes-v1.0.0.md" "$STAGE/docs/release-notes-v1.0.0.md"

for doc in admin-guide.md troubleshooting.md key-management.md offline-lifecycle.md; do
  if [ -f "$ROOT/docs/$doc" ]; then
    cp "$ROOT/docs/$doc" "$STAGE/docs/$doc"
  fi
done

printf '%s\n' "$VERSION" > "$STAGE/release-version.txt"
printf '%s\n' "$backend_arch" > "$STAGE/release-arch.txt"
cat > "$STAGE/release-manifest.json" <<EOF
{
  "schema_version": 1,
  "product": "harbor-transfer-portal",
  "version": "$VERSION",
  "architecture": "$backend_arch",
  "source_revision": "$source_revision",
  "images": [
    "$BACKEND_IMAGE",
    "$FRONTEND_IMAGE"
  ],
  "components": {
    "helm": "$helm_version",
    "skopeo_debian_package": "$skopeo_package"
  },
  "release_notes": "docs/release-notes-v1.0.0.md"
}
EOF

(
  cd "$STAGE"
  find . -type f ! -name CHECKSUMS.sha256 -print \
    | LC_ALL=C sort \
    | sed 's#^\./##' \
    | while IFS= read -r file; do
        sha256sum "$file"
      done > CHECKSUMS.sha256
)

ARCHIVE="$OUT_DIR/$PACKAGE_NAME.tar.gz"
tar -C "$TMP" -czf "$ARCHIVE" "$PACKAGE_NAME"
(
  cd "$OUT_DIR"
  sha256sum "$(basename "$ARCHIVE")" > "$(basename "$ARCHIVE").sha256"
)

printf 'Offline kit created:\n  %s\n  %s.sha256\n' "$ARCHIVE" "$ARCHIVE"
