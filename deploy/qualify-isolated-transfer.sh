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

dump_target_failure() {
  container_id=$(
    HTP_ACCEPTANCE_IMAGE="$ACCEPTANCE_IMAGE" HTP_REGISTRY_IMAGE="$REGISTRY_IMAGE" HTP_TRANSFER_DIR="$PHYSICAL" \
      docker compose -p "$TARGET_PROJECT" -f "$COMPOSE" --profile target ps -a -q target-runner 2>/dev/null \
      || true
  )
  [ -n "$container_id" ] || return 0

  database="$TMP/target-failure.db"
  docker cp "$container_id:/tmp/htp-isolated-target/portal.db" "$database" >/dev/null 2>&1 || return 0

  python3 - "$database" <<'PY' || true
import sqlite3
import sys

connection = sqlite3.connect(sys.argv[1])
connection.row_factory = sqlite3.Row
try:
    print("TARGET persisted operation diagnostics:")
    for row in connection.execute(
        """
        SELECT id, status, error_code, error_message, source_delivery_id,
               total_artifacts, successful_artifacts, failed_artifacts,
               skipped_artifacts, conflict_artifacts
        FROM operations
        ORDER BY id
        """
    ):
        print("  operation", dict(row))

    print("TARGET persisted artifact diagnostics:")
    for row in connection.execute(
        """
        SELECT operation_id, artifact_type, repository, name, reference, version,
               source_digest, target_digest, status, error_code, error_message
        FROM artifact_results
        ORDER BY operation_id, id
        """
    ):
        print("  artifact", dict(row))
finally:
    connection.close()
PY
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
[ "$file_count" = 6 ] || fail "SOURCE exported unexpected handoff file count: $file_count"
archive=$(find "$SOURCE_OUT" -maxdepth 1 -type f -name '*.htp.tar.gz' -print)
[ -n "$archive" ] || fail 'SOURCE did not produce transfer bundle'
archive_base=$(basename "$archive")
[ -f "$SOURCE_OUT/$archive_base.sha256" ] || fail 'SOURCE did not produce bundle sidecar'
delivery_id=${archive_base%.htp.tar.gz}
handoff="$SOURCE_OUT/$delivery_id.htp-handoff.json"
[ -f "$handoff" ] || fail 'SOURCE did not produce signed handoff'
bootstrap_trust="$SOURCE_OUT/bootstrap.htp-trust.tar.gz"
rotation_trust="$SOURCE_OUT/rotation.htp-trust.tar.gz"
fingerprint_file="$SOURCE_OUT/source-fingerprint.out-of-band.txt"
[ -f "$bootstrap_trust" ] || fail 'SOURCE did not produce bootstrap trust package'
[ -f "$rotation_trust" ] || fail 'SOURCE did not produce rotation trust package'
[ -f "$fingerprint_file" ] || fail 'SOURCE did not produce out-of-band fingerprint fixture'
expected_fingerprint=$(tr -d '\r\n' < "$fingerprint_file")
case "$expected_fingerprint" in
  sha256:????????????????????????????????????????????????????????????????) ;;
  *) fail 'SOURCE produced invalid out-of-band fingerprint' ;;
esac

# Publication files keep their production ownership/mode. The physical transport
# is a no-network copier that can read UID 10001-owned 0440 files without changing
# SOURCE permissions. Only copied media is made readable by the TARGET runtime.
docker run --rm \
  --network none \
  --user 0 \
  --entrypoint /bin/sh \
  -e HTP_BUNDLE_NAME="$archive_base" \
  -e HTP_HANDOFF_NAME="$delivery_id.htp-handoff.json" \
  -v "$SOURCE_OUT:/source:ro" \
  -v "$PHYSICAL:/physical" \
  "$ACCEPTANCE_IMAGE" \
  -c 'set -eu
      cp "/source/$HTP_BUNDLE_NAME" "/physical/$HTP_BUNDLE_NAME"
      cp "/source/$HTP_BUNDLE_NAME.sha256" "/physical/$HTP_BUNDLE_NAME.sha256"
      cp "/source/$HTP_HANDOFF_NAME" "/physical/$HTP_HANDOFF_NAME"
      cp /source/bootstrap.htp-trust.tar.gz /physical/bootstrap.htp-trust.tar.gz
      cp /source/rotation.htp-trust.tar.gz /physical/rotation.htp-trust.tar.gz
      chmod 0444         "/physical/$HTP_BUNDLE_NAME"         "/physical/$HTP_BUNDLE_NAME.sha256"         "/physical/$HTP_HANDOFF_NAME"         /physical/bootstrap.htp-trust.tar.gz         /physical/rotation.htp-trust.tar.gz'

physical_count=$(find "$PHYSICAL" -maxdepth 1 -type f | wc -l | tr -d ' ')
[ "$physical_count" = 4 ] \
  || fail 'physical transfer contains files outside bundle/sidecar/two trust packages'
[ ! -e "$PHYSICAL/source-fingerprint.out-of-band.txt" ] \
  || fail 'out-of-band SOURCE fingerprint leaked onto physical media'

printf 'TARGET phase: SOURCE is gone; importing only physically copied material...\n'
set +e
HTP_ACCEPTANCE_IMAGE="$ACCEPTANCE_IMAGE" \
  HTP_REGISTRY_IMAGE="$REGISTRY_IMAGE" \
  HTP_TRANSFER_DIR="$PHYSICAL" \
  HTP_ACCEPTANCE_EXPECTED_FINGERPRINT="$expected_fingerprint" \
  docker compose -p "$TARGET_PROJECT" -f "$COMPOSE" --profile target \
  up --abort-on-container-exit --exit-code-from target-runner target-runner
target_run_status=$?
set -e
if [ "$target_run_status" -ne 0 ]; then
  dump_target_failure
  exit "$target_run_status"
fi

HTP_ACCEPTANCE_IMAGE="$ACCEPTANCE_IMAGE" HTP_REGISTRY_IMAGE="$REGISTRY_IMAGE" HTP_TRANSFER_DIR="$PHYSICAL" \
  docker compose -p "$TARGET_PROJECT" -f "$COMPOSE" --profile target \
  down --remove-orphans --volumes

printf 'Isolated SOURCE -> physical bundle -> TARGET acceptance passed.\n'
