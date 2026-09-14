#!/usr/bin/env bash
set -euo pipefail

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 2
}

usage() {
  cat >&2 <<'EOF'
Usage:
  ./deploy/build-offline-kit.sh VERSION [OUTPUT_DIR]

Example:
  ./deploy/build-offline-kit.sh 1.0.0 dist

The build host may use network access to build application images. The resulting
offline kit is self-contained for runtime installation and performs no build/pull.
EOF
  exit 2
}

version=${1:-}
output_dir=${2:-dist}
[[ $# -ge 1 && $# -le 2 ]] || usage

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "${script_dir}/.." && pwd)"
cd "${repo_root}"

command -v python3 >/dev/null 2>&1 || fail "python3 is required on the release build host"
command -v docker >/dev/null 2>&1 || fail "docker is required on the release build host"
command -v git >/dev/null 2>&1 || fail "git is required on the release build host"

python3 - "${version}" <<'PY'
import sys

from deploy.offline_release import ReleaseError, validate_version

try:
    validate_version(sys.argv[1])
except ReleaseError as exc:
    print(f"ERROR: {exc}", file=sys.stderr)
    raise SystemExit(2)
PY

docker info >/dev/null 2>&1 || fail "Docker Engine is unavailable"
docker compose version >/dev/null 2>&1 || fail "Docker Compose plugin is unavailable"

git diff --quiet -- || fail "working tree has unstaged changes; release source must be clean"
git diff --cached --quiet -- || fail "working tree has staged changes; release source must be clean"
source_commit="$(git rev-parse HEAD)"
[[ "${source_commit}" =~ ^[0-9a-f]{40}$ ]] || fail "cannot determine full source commit"

backend_ref="harbor-transfer-portal-backend:${version}"
frontend_ref="harbor-transfer-portal-frontend:${version}"

printf 'Building release images for %s...\n' "${version}"
PORTAL_VERSION="${version}" docker compose -f compose.yaml build backend frontend

backend_arch="$(docker image inspect --format '{{.Architecture}}' "${backend_ref}")"
frontend_arch="$(docker image inspect --format '{{.Architecture}}' "${frontend_ref}")"
[[ -n "${backend_arch}" ]] || fail "cannot determine backend image architecture"
[[ "${backend_arch}" == "${frontend_arch}" ]] || \
  fail "backend/frontend architecture mismatch: ${backend_arch} vs ${frontend_arch}"
case "${backend_arch}" in
  amd64|arm64) ;;
  *) fail "unsupported built image architecture: ${backend_arch}" ;;
esac

tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/htp-offline-kit.XXXXXX")"
cleanup() {
  rm -rf -- "${tmp_dir}"
}
trap cleanup EXIT INT TERM

backend_tar="${tmp_dir}/backend.tar"
frontend_tar="${tmp_dir}/frontend.tar"

printf 'Exporting prebuilt images...\n'
docker save --output "${backend_tar}" "${backend_ref}"
docker save --output "${frontend_tar}" "${frontend_ref}"

[[ -s "${backend_tar}" ]] || fail "backend image archive is empty"
[[ -s "${frontend_tar}" ]] || fail "frontend image archive is empty"

skopeo_version="$(docker run --rm --network none --entrypoint skopeo "${backend_ref}" --version)"
helm_version="$(docker run --rm --network none --entrypoint helm "${backend_ref}" version --short)"
[[ -n "${skopeo_version}" ]] || fail "cannot determine Skopeo version from backend image"
[[ -n "${helm_version}" ]] || fail "cannot determine Helm version from backend image"

mkdir -p -- "${output_dir}"

archive="$(
  python3 deploy/offline_release.py package \
    --repo-root "${repo_root}" \
    --output-dir "${output_dir}" \
    --version "${version}" \
    --architecture "${backend_arch}" \
    --source-commit "${source_commit}" \
    --backend-image-tar "${backend_tar}" \
    --frontend-image-tar "${frontend_tar}" \
    --skopeo-version "${skopeo_version}" \
    --helm-version "${helm_version}"
)"

[[ -f "${archive}" ]] || fail "release packager did not produce archive: ${archive}"
[[ -f "${archive}.sha256" ]] || fail "release packager did not produce checksum sidecar"

printf '\nOffline release kit created:\n  %s\n  %s\n' "${archive}" "${archive}.sha256"
printf 'Architecture: %s\nSource commit: %s\n' "${backend_arch}" "${source_commit}"
