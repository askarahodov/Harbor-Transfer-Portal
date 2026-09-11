#!/bin/sh
set -eu

if [ ! -f .env ]; then
    echo '.env is required; copy .env.example to .env and configure the local contour first.' >&2
    exit 2
fi

cleanup() {
    set +e
    docker compose down >/dev/null 2>&1
}
trap cleanup EXIT INT TERM

wait_backend() {
    attempts=0
    while [ "$attempts" -lt 30 ]; do
        if docker compose exec -T backend python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2).read()" >/dev/null 2>&1; then
            return 0
        fi
        attempts=$((attempts + 1))
        sleep 2
    done
    echo 'Backend health check did not become ready.' >&2
    return 1
}

wait_frontend() {
    attempts=0
    while [ "$attempts" -lt 30 ]; do
        if docker compose exec -T frontend wget -q -O /dev/null http://127.0.0.1/healthz >/dev/null 2>&1; then
            return 0
        fi
        attempts=$((attempts + 1))
        sleep 2
    done
    echo 'Frontend health check did not become ready.' >&2
    return 1
}

docker compose config >/dev/null
docker compose up -d --build
wait_backend
wait_frontend

docker compose exec -T frontend wget -q -O - http://127.0.0.1/api/health | grep -F '"status":"ok"' >/dev/null
docker compose exec -T backend sh -c "skopeo --version | grep -F '1.9.3' >/dev/null"
docker compose exec -T backend sh -c "helm version --short | grep -F 'v3.22.0' >/dev/null"

docker compose exec -T backend sh -c "printf 'persistent\n' > /app/data/.compose-smoke"
docker compose restart >/dev/null
wait_backend
wait_frontend
docker compose exec -T backend test -f /app/data/.compose-smoke
docker compose exec -T backend rm /app/data/.compose-smoke

printf '%s\n' 'Compose smoke test passed: proxy health, tool versions and named-volume persistence verified.'
