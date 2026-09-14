#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd -P)
COMPOSE="$ROOT/deploy/compose-isolated-transfer-acceptance.yml"
REGISTRY_IMAGE=${HTP_REGISTRY_IMAGE:-registry:2.8.3@sha256:a3d8aaa63ed8681a604f1dea0aa03f100d5895b6a58ace528858a7b332415373}
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
  original_status=$?
  trap - EXIT HUP INT TERM
  set +e
  HTP_ACCEPTANCE_IMAGE="$ACCEPTANCE_IMAGE" HTP_REGISTRY_IMAGE="$REGISTRY_IMAGE" HTP_TRANSFER_DIR="$SOURCE_OUT" \
    docker compose -p "$SOURCE_PROJECT" -f "$COMPOSE" --profile source \
    down --remove-orphans --volumes >/dev/null 2>&1
  source_cleanup_status=$?
  HTP_ACCEPTANCE_IMAGE="$ACCEPTANCE_IMAGE" HTP_REGISTRY_IMAGE="$REGISTRY_IMAGE" HTP_TRANSFER_DIR="$PHYSICAL" \
    docker compose -p "$TARGET_PROJECT" -f "$COMPOSE" --profile target \
    down --remove-orphans --volumes >/dev/null 2>&1
  target_cleanup_status=$?
  docker image rm -f "$ACCEPTANCE_IMAGE" >/dev/null 2>&1
  image_cleanup_status=$?
  rm -rf "$TMP"
  temp_cleanup_status=$?
  set -e

  if [ "$original_status" -ne 0 ]; then
    exit "$original_status"
  fi
  for cleanup_status in \
    "$source_cleanup_status" \
    "$target_cleanup_status" \
    "$image_cleanup_status" \
    "$temp_cleanup_status"; do
    if [ "$cleanup_status" -ne 0 ]; then
      exit "$cleanup_status"
    fi
  done
  exit 0
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

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
  docker compose -p "$SOURCE_PROJECT" -f "$COMPOSE" --profile source \
  down --remove-orphans --volumes

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

# Publication files keep their production ownership/mode. The physical transport
# is a no-network copier that can read UID 10001-owned 0440 files without changing
# SOURCE permissions. Only copied media is made readable by the TARGET runtime.
docker run --rm \
  --network none \
  --user 0 \
  --entrypoint /bin/sh \
  -e HTP_BUNDLE_NAME="$archive_base" \
  -v "$SOURCE_OUT:/source:ro" \
  -v "$PHYSICAL:/physical" \
  "$ACCEPTANCE_IMAGE" \
  -c 'set -eu
      cp "/source/$HTP_BUNDLE_NAME" "/physical/$HTP_BUNDLE_NAME"
      cp "/source/$HTP_BUNDLE_NAME.sha256" "/physical/$HTP_BUNDLE_NAME.sha256"
      cp /source/source-public.pem /physical/source-public.pem
      chmod 0444 "/physical/$HTP_BUNDLE_NAME" "/physical/$HTP_BUNDLE_NAME.sha256" /physical/source-public.pem'

physical_count=$(find "$PHYSICAL" -maxdepth 1 -type f | wc -l | tr -d ' ')
[ "$physical_count" = 3 ] \
  || fail 'physical transfer contains files outside bundle/sidecar/public trust material'

printf 'TARGET phase: SOURCE is gone; importing only physically copied material...\n'
HTP_ACCEPTANCE_IMAGE="$ACCEPTANCE_IMAGE" HTP_REGISTRY_IMAGE="$REGISTRY_IMAGE" HTP_TRANSFER_DIR="$PHYSICAL" \
  docker compose -p "$TARGET_PROJECT" -f "$COMPOSE" --profile target \
  up --abort-on-container-exit --exit-code-from target-runner target-runner
HTP_ACCEPTANCE_IMAGE="$ACCEPTANCE_IMAGE" HTP_REGISTRY_IMAGE="$REGISTRY_IMAGE" HTP_TRANSFER_DIR="$PHYSICAL" \
  docker compose -p "$TARGET_PROJECT" -f "$COMPOSE" --profile target \
  down --remove-orphans --volumes

printf 'Isolated SOURCE -> physical bundle -> TARGET acceptance passed.\n'
