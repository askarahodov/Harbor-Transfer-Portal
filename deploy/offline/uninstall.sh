#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd -P)
INSTALL_INPUT=$SCRIPT_DIR
PURGE_DATA=0
INSTALL_SET=0

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 2
}

for arg in "$@"; do
  case "$arg" in
    --purge-data)
      PURGE_DATA=1
      ;;
    --*)
      fail "unknown option: $arg"
      ;;
    *)
      [ "$INSTALL_SET" -eq 0 ] || fail 'only one install directory may be supplied'
      INSTALL_INPUT=$arg
      INSTALL_SET=1
      ;;
  esac
done

[ -d "$INSTALL_INPUT" ] || fail "install directory not found: $INSTALL_INPUT"
INSTALL_DIR=$(CDPATH= cd -- "$INSTALL_INPUT" && pwd -P)
cd "$INSTALL_DIR"

[ -f compose.yaml ] || fail 'compose.yaml is missing from install directory'
[ -e .env ] || fail '.env is missing from install directory'
[ -f .env ] && [ ! -L .env ] || fail 'existing .env must be a regular non-symlink file'
command -v docker >/dev/null 2>&1 || fail 'required command not found: docker'
docker info >/dev/null 2>&1 || fail 'Docker Engine is not available'
docker compose version >/dev/null 2>&1 || fail 'Docker Compose v2 is not available'

if [ "$PURGE_DATA" -eq 1 ]; then
  [ "${PORTAL_CONFIRM_PURGE:-}" = 'DELETE_PORTAL_DATA' ] || \
    fail 'data purge requires PORTAL_CONFIRM_PURGE=DELETE_PORTAL_DATA'
  printf 'Stopping portal and deleting persistent Docker volume...\n'
  docker compose --env-file "$INSTALL_DIR/.env" -f "$INSTALL_DIR/compose.yaml" \
    down --remove-orphans --volumes
  printf 'Persistent Docker volume removed. .env and installation files were preserved for manual handling.\n'
else
  printf 'Stopping portal; persistent data and .env will be preserved...\n'
  docker compose --env-file "$INSTALL_DIR/.env" -f "$INSTALL_DIR/compose.yaml" \
    down --remove-orphans
  printf 'Uninstall completed. Persistent volume harbor-transfer-portal_portal-data and .env were preserved.\n'
fi
