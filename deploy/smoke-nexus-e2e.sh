#!/usr/bin/env bash
# Disposable real-Nexus CI smoke. No production registry is contacted.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

suffix="${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}"
container="htp-nexus-e2e-${suffix}"
network="htp-nexus-e2e-${suffix}"
volume="htp-nexus-e2e-data-${suffix}"
image="htp-nexus-e2e:${suffix}"

cleanup() {
  result=$?
  trap - EXIT
  if [[ "$result" -ne 0 ]]; then
    docker logs --tail 80 "$container" 2>/dev/null || true
  fi
  docker rm -f "$container" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
  docker volume rm "$volume" >/dev/null 2>&1 || true
  exit "$result"
}
trap cleanup EXIT

docker network create --internal "$network" >/dev/null
docker volume create "$volume" >/dev/null
# Last pre-Community-Edition OSS release: no new CE EULA acceptance needed.
# Test baseline compatibility only; modern CE E2E requires separate EULA approval.
docker run --detach --name "$container" \
  --network "$network" --network-alias nexus \
  --volume "$volume:/nexus-data" \
  --env 'INSTALL4J_ADD_VM_PARAMS=-Xms512m -Xmx2048m -XX:MaxDirectMemorySize=1024m -Djava.util.prefs.userRoot=/nexus-data/javaprefs' \
  sonatype/nexus3:3.76.1 >/dev/null

# Build the exact application image being tested, with its pinned Helm/Skopeo tools.
docker build --file backend/Dockerfile --tag "$image" .

# The internal Docker network intentionally has no host-facing route.
# Wait for Nexus's bootstrap secret, then perform all REST requests from a peer
# container in that same private network (rather than from the GitHub runner host).
password=""
for _ in $(seq 1 120); do
  if docker exec "$container" test -s /nexus-data/admin.password >/dev/null 2>&1; then
    password="$(docker exec "$container" cat /nexus-data/admin.password)"
    break
  fi
  sleep 5
done
if [[ -z "$password" ]]; then
  echo "Nexus bootstrap password not present" >&2
  exit 1
fi
if [[ -n "${GITHUB_ACTIONS:-}" ]]; then
  echo "::add-mask::${password}"
fi

docker run --rm \
  --network "$network" \
  --volume "$ROOT/backend/integration:/app/integration:ro" \
  --env "NEXUS_E2E_PASSWORD=${password}" \
  --entrypoint python \
  "$image" /app/integration/configure_nexus_e2e.py

echo "Disposable Nexus Docker hosted registry is configured"
docker run --rm \
  --network "$network" \
  --volume "$ROOT/backend/integration:/app/integration:ro" \
  --env "NEXUS_E2E_PASSWORD=${password}" \
  --entrypoint python \
  "$image" /app/integration/nexus_real_smoke.py
