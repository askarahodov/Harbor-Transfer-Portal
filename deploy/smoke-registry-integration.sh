#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
COMPOSE_FILE="$ROOT_DIR/deploy/compose-registry-integration.yml"
PROJECT_NAME="htp-registry-integration-${GITHUB_RUN_ID:-$$}"

cleanup() {
    original_status=$?
    trap - EXIT HUP INT TERM
    set +e
    docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" down --volumes --remove-orphans
    cleanup_status=$?
    set -e

    if [ "$original_status" -ne 0 ]; then
        exit "$original_status"
    fi
    exit "$cleanup_status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

cd "$ROOT_DIR"

docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" pull registry
docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" build integration
docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" up \
    --abort-on-container-exit \
    --exit-code-from integration \
    integration
