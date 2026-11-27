#!/bin/sh
# Exercise the source Linux launcher with a fake Docker CLI. No actual
# containers/images/volumes are touched. Run in an ephemeral checkout only.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
[ ! -e .env ] && [ ! -L .env ] || {
  echo 'Refusing launcher smoke: checkout already contains .env' >&2
  exit 2
}
TMP=$(mktemp -d)
cleanup() {
  rm -f .env
  rm -rf "$TMP"
}
trap cleanup EXIT HUP INT TERM
cat > "$TMP/docker" <<'DOCKER'
#!/bin/sh
printf 'revision=%s args=%s\n' "${PORTAL_VCS_REF:-unset}" "$*" >> "$HTP_MOCK_LOG"
if [ "$1" = info ] && [ "${2:-}" = --format ]; then
  printf '%s\n' "${HTP_MOCK_OS:-linux}"
fi
exit 0
DOCKER
chmod +x "$TMP/docker"
export HTP_MOCK_LOG="$TMP/docker-calls.txt"
export HTP_MOCK_OS=linux
PATH="$TMP:$PATH"
export PATH
./run.sh doctor
[ ! -e .env ] || { echo 'doctor must be read-only' >&2; exit 1; }
./run.sh
[ -f .env ] || { echo 'up did not initialize .env' >&2; exit 1; }
revision=$(git rev-parse --verify HEAD)
grep -F "revision=$revision args=compose build" "$HTP_MOCK_LOG" >/dev/null
grep -F "revision=$revision args=compose up -d --no-build --pull never --wait --wait-timeout 180" "$HTP_MOCK_LOG" >/dev/null
baseline=$(sha256sum .env | cut -d' ' -f1)
./run.sh build-plain
grep -F "revision=$revision args=compose --progress plain build" "$HTP_MOCK_LOG" >/dev/null
./run.sh build-clean
grep -F "revision=$revision args=compose --progress plain build --no-cache" "$HTP_MOCK_LOG" >/dev/null
[ "$(grep -c 'args=compose up -d ' "$HTP_MOCK_LOG")" -eq 1 ] || {
  echo 'build-only commands must not start or recreate containers' >&2
  exit 1
}
./run.sh start
./run.sh status
./run.sh stop
[ "$baseline" = "$(sha256sum .env | cut -d' ' -f1)" ] || {
  echo 'launcher overwrote an existing .env' >&2
  exit 1
}
! grep 'args=compose down -v' "$HTP_MOCK_LOG" >/dev/null
HTP_MOCK_OS=windows
export HTP_MOCK_OS
if ./run.sh doctor >/dev/null 2>&1; then
  echo 'launcher accepted a Windows-container engine' >&2
  exit 1
fi
printf 'Linux source launcher smoke PASS (mocked Docker, SHA preserved, data untouched)\n'
