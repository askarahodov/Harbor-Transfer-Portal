#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd -P)
COMPOSE="$ROOT/deploy/compose-isolated-transfer-acceptance.yml"
REGISTRY_IMAGE=${HTP_REGISTRY_IMAGE:-registry:2.8.3@sha256:5895965c81f2a5bb3d5a0d28d3a1324c9e1d6f9e07e7bd6e60e56f4a0c1d37e6}
TAG=${GITHUB_RUN_ID:-local}-$$
ACCEPTANCE_IMAGE="harbor-transfer-portal-acceptance:$TAG"
SOURCE_PROJECT="htp-source-$TAG"
TARGET_PROJECT="htp-target-$TAG"
TMP=$(mktemp -d)
SOURCE_OUT="$TMP/source-out"
PHYSICAL="$TMP/physical-transfer"
mkdir -p "$SOURCE_OUT" "$PHYSICAL"
chmod 0777 "$SOURCE_OUT" "$PHYSICAL"

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

cleanup() {
  status=$?
  set +e
  HTP_ACCEPTANCE_IMAGE="$ACCEPTANCE_IMAGE" HTP_REGISTRY_IMAGE="$REGISTRY_IMAGE" HTP_TRANSFER_DIR="$SOURCE_OUT" \
    docker compose -p "$SOURCE_PROJECT" -f "$COMPOSE" --profile source down --remove-orphans --volumes >/dev/null 2>&1
  HTP_ACCEPTANCE_IMAGE="$ACCEPTANCE_IMAGE" HTP_REGISTRY_IMAGE="$REGISTRY_IMAGE" HTP_TRANSFER_DIR="$PHYSICAL" \
    docker compose -p "$TARGET_PROJECT" -f "$COMPOSE" --profile target down --remove-orphans --volumes >/dev/null 2>&1
  docker image rm -f "$ACCEPTANCE_IMAGE" >/dev/null 2>&1
  rm -rf "$TMP"
  trap - EXIT HUP INT TERM
  exit "$status"
}
trap cleanup EXIT HUP INT TERM

command -v docker >/dev/null 2>&1 || fail 'docker is required'
docker info >/dev/null 2>&1 || fail 'Docker Engine is unavailable'
docker compose version >/dev/null 2>&1 || fail 'Docker Compose v2 is unavailable'

cd "$ROOT"
printf 'Build phase: preparing pinned local fixtures before the isolated runtime boundary...\n'
docker pull "$REGISTRY_IMAGE"
docker build -f backend/Dockerfile -t "$ACCEPTANCE_IMAGE" .

printf 'SOURCE phase: real export through isolated SOURCE registry...\n'
HTP_ACCEPTANCE_IMAGE="$ACCEPTANCE_IMAGE" HTP_REGISTRY_IMAGE="$REGISTRY_IMAGE" HTP_TRANSFER_DIR="$SOURCE_OUT" \
  docker compose -p "$SOURCE_PROJECT" -f "$COMPOSE" --profile source \
  up --abort-on-container-exit --exit-code-from source-runner source-runner
HTP_ACCEPTANCE_IMAGE="$ACCEPTANCE_IMAGE" HTP_REGISTRY_IMAGE="$REGISTRY_IMAGE" HTP_TRANSFER_DIR="$SOURCE_OUT" \
  docker compose -p "$SOURCE_PROJECT" -f "$COMPOSE" --profile source down --remove-orphans --volumes

if docker ps -a --format '{{.Names}}' | grep -F "$SOURCE_PROJECT" >/dev/null; then
  fail 'SOURCE project still has containers after physical-transfer boundary'
fi

file_count=$(find "$SOURCE_OUT" -maxdepth 1 -type f | wc -l | tr -d ' ')
[ "$file_count" = 3 ] || fail "SOURCE exported unexpected physical payload file count: $file_count"
archive=$(find "$SOURCE_OUT" -maxdepth 1 -type f -name '*.htp.tar.gz' -print)
[ -n "$archive" ] || fail 'SOURCE did not produce transfer bundle'
archive_base=$(basename "$archive")
[ -f "$SOURCE_OUT/$archive_base.sha256" ] || fail 'SOURCE did not produce bundle sidecar'
[ -f "$SOURCE_OUT/source-public.pem" ] || fail 'SOURCE did not produce public trust material'

# This copy is the only bridge between contour phases. No SOURCE registry/container survives it.
cp "$SOURCE_OUT/$archive_base" "$PHYSICAL/$archive_base"
cp "$SOURCE_OUT/$archive_base.sha256" "$PHYSICAL/$archive_base.sha256"
cp "$SOURCE_OUT/source-public.pem" "$PHYSICAL/source-public.pem"

physical_count=$(find "$PHYSICAL" -maxdepth 1 -type f | wc -l | tr -d ' ')
[ "$physical_count" = 3 ] || fail 'physical transfer contains files outside bundle/sidecar/public trust material'

printf 'TARGET phase: SOURCE is gone; importing only physically copied material...\n'
HTP_ACCEPTANCE_IMAGE="$ACCEPTANCE_IMAGE" HTP_REGISTRY_IMAGE="$REGISTRY_IMAGE" HTP_TRANSFER_DIR="$PHYSICAL" \
  docker compose -p "$TARGET_PROJECT" -f "$COMPOSE" --profile target \
  up --abort-on-container-exit --exit-code-from target-runner target-runner
HTP_ACCEPTANCE_IMAGE="$ACCEPTANCE_IMAGE" HTP_REGISTRY_IMAGE="$REGISTRY_IMAGE" HTP_TRANSFER_DIR="$PHYSICAL" \
  docker compose -p "$TARGET_PROJECT" -f "$COMPOSE" --profile target down --remove-orphans --volumes

printf 'Isolated SOURCE -> physical bundle -> TARGET acceptance passed.\n'
