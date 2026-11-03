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
api="http://127.0.0.1:18081/service/rest/v1"

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
docker run --detach --name "$container" \
  --network "$network" --network-alias nexus \
  --publish 127.0.0.1:18081:8081 \
  --volume "$volume:/nexus-data" \
  --env 'INSTALL4J_ADD_VM_PARAMS=-Xms512m -Xmx2048m -XX:MaxDirectMemorySize=1024m -Djava.util.prefs.userRoot=/nexus-data/javaprefs' \
  sonatype/nexus3:3.89.2 >/dev/null

# Build the exact application image being tested, with its pinned Helm/Skopeo tools.
docker build --file backend/Dockerfile --tag "$image" .

ready=0
for _ in $(seq 1 120); do
  if curl --fail --silent --show-error --max-time 2 "$api/status" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 5
done
if [[ "$ready" != "1" ]]; then
  echo "Nexus did not become ready" >&2
  exit 1
fi

password="$(docker exec "$container" cat /nexus-data/admin.password)"
if [[ -z "$password" ]]; then
  echo "Nexus bootstrap password not present" >&2
  exit 1
fi
# Do not expose the generated container administrator password in job logs.
if [[ -n "${GITHUB_ACTIONS:-}" ]]; then
  echo "::add-mask::${password}"
fi

# Authenticate to the real Nexus REST API and activate the required Docker realm.
realms="$(curl --fail --silent --show-error --user "admin:${password}" "$api/security/realms/active")"
realms="$(printf '%s' "$realms" | jq --compact-output 'if index("DockerToken") then . else . + ["DockerToken"] end')"
curl --fail --silent --show-error --output /dev/null \
  --user "admin:${password}" \
  --request PUT \
  --header "Content-Type: application/json" \
  --data "$realms" \
  "$api/security/realms/active"

# The docker connector exposes a separate registry root at http://nexus:5000/v2/.
repository='{
  "name": "e2e-docker-hosted",
  "online": true,
  "storage": {
    "blobStoreName": "default",
    "strictContentTypeValidation": true,
    "writePolicy": "allow"
  },
  "cleanup": {"policyNames": []},
  "component": {"proprietaryComponents": false},
  "docker": {
    "v1Enabled": false,
    "forceBasicAuth": true,
    "httpPort": 5000
  }
}'
curl --fail-with-body --silent --show-error --output /dev/null \
  --user "admin:${password}" \
  --request POST \
  --header "Content-Type: application/json" \
  --data "$repository" \
  "$api/repositories/docker/hosted"

echo "Disposable Nexus Docker hosted registry is configured"
docker run --rm \
  --network "$network" \
  --volume "$ROOT/backend/integration:/app/integration:ro" \
  --env "NEXUS_E2E_PASSWORD=${password}" \
  --entrypoint python \
  "$image" /app/integration/nexus_real_smoke.py
