#!/bin/sh
set -eu

if [ ! -f .env ]; then
    echo 'Требуется .env; скопируйте .env.example в .env и настройте локальный контур.' >&2
    exit 2
fi

jwt_secret=$(grep '^JWT_SECRET=' .env | tail -n 1 | cut -d= -f2-)
if [ "${#jwt_secret}" -lt 32 ]; then
    echo 'Для smoke test требуется JWT_SECRET длиной не менее 32 символов.' >&2
    exit 2
fi
unset jwt_secret

current_contour=$(grep '^PORTAL_CONTOUR=' .env | tail -n 1 | cut -d= -f2-)
case "$current_contour" in
    SOURCE) opposite_contour=TARGET ;;
    TARGET) opposite_contour=SOURCE ;;
    *)
        echo 'PORTAL_CONTOUR в .env должен быть SOURCE или TARGET.' >&2
        exit 2
        ;;
esac

frontend_container_base="http://127.0.0.1:8080"

cleanup() {
    set +e
    docker compose down >/dev/null 2>&1
}
trap cleanup EXIT

wait_backend() {
    attempts=0
    while [ "$attempts" -lt 30 ]; do
        if docker compose exec -T backend python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2).read()" >/dev/null 2>&1; then
            return 0
        fi
        attempts=$((attempts + 1))
        sleep 2
    done
    echo 'Backend не перешёл в healthy-состояние.' >&2
    return 1
}

wait_frontend() {
    attempts=0
    while [ "$attempts" -lt 30 ]; do
        if docker compose exec -T frontend wget -q -O /dev/null "${frontend_container_base}/healthz" >/dev/null 2>&1; then
            return 0
        fi
        attempts=$((attempts + 1))
        sleep 2
    done
    echo 'Frontend не перешёл в healthy-состояние.' >&2
    return 1
}

compose_config=$(docker compose config)
logging_driver_count=$(printf '%s\n' "$compose_config" | awk '/driver: json-file/ {count++} END {print count+0}')
logging_max_size_count=$(printf '%s\n' "$compose_config" | awk '/max-size:/ {count++} END {print count+0}')
logging_max_file_count=$(printf '%s\n' "$compose_config" | awk '/max-file:/ {count++} END {print count+0}')
host_network_count=$(printf '%s\n' "$compose_config" | awk '/network_mode: host/ {count++} END {print count+0}')
if [ "$logging_driver_count" -ne 2 ] || [ "$logging_max_size_count" -ne 2 ] || [ "$logging_max_file_count" -ne 2 ]; then
    echo 'Backend и frontend должны иметь bounded json-file logging policy.' >&2
    exit 1
fi
if [ "$host_network_count" -ne 0 ]; then
    echo 'Application runtime Compose не должен использовать host network.' >&2
    exit 1
fi
unset compose_config logging_driver_count logging_max_size_count logging_max_file_count host_network_count

docker compose up -d --build
wait_backend
wait_frontend

frontend_id=$(docker compose ps -q frontend)
backend_id=$(docker compose ps -q backend)
frontend_bindings=$(docker inspect --format '{{json .HostConfig.PortBindings}}' "$frontend_id")
backend_bindings=$(docker inspect --format '{{json .HostConfig.PortBindings}}' "$backend_id")
case "$frontend_bindings" in
    *'"8080/tcp"'*) ;;
    *)
        echo "Frontend port 8080 должен быть опубликован на host: $frontend_bindings" >&2
        exit 1
        ;;
esac
case "$backend_bindings" in
    *'"8000/tcp"'*)
        echo "Backend port 8000 не должен публиковаться на host: $backend_bindings" >&2
        exit 1
        ;;
esac
unset frontend_id backend_id frontend_bindings backend_bindings
docker compose exec -T frontend wget -q -O - "http://backend:8000/api/health" | grep -F '"status":"ok"' >/dev/null

docker compose exec -T frontend wget -q -O - "${frontend_container_base}/api/health" | grep -F '"status":"ok"' >/dev/null
docker compose exec -T frontend wget -q -O - "${frontend_container_base}/runtime-config.js" | grep -F "contour: '${current_contour}'" >/dev/null

# Documentation is served by the same frontend image and must remain fully offline.
docker compose exec -T frontend wget -q -O - "${frontend_container_base}/docs/" | grep -F 'Harbor Transfer Portal — Документация' >/dev/null
docker compose exec -T frontend wget -q -O - "${frontend_container_base}/docs/_sidebar.md" | grep -F '/docs/dashboard.md' >/dev/null
docker compose exec -T frontend wget -q -O - "${frontend_container_base}/docs/dashboard.md" | grep -F '# Dashboard' >/dev/null
docker compose exec -T frontend wget -q -O - "${frontend_container_base}/docs/settings.md" | grep -F '# Настройки Portal' >/dev/null
docker compose exec -T frontend wget -q -O /dev/null "${frontend_container_base}/docs/_vendor/docsify.min.js"
docker compose exec -T frontend wget -q -O /dev/null "${frontend_container_base}/docs/_vendor/search.min.js"
docker compose exec -T frontend wget -q -O - "${frontend_container_base}/docs/_portal/tokens.css" | grep -F -- '--color-brand-surface' >/dev/null
docker compose exec -T frontend wget -q -O - "${frontend_container_base}/docs/portal-docs.css" | grep -F -- '--docs-content-max: 1180px' >/dev/null
if docker compose exec -T frontend wget -q -O /dev/null "${frontend_container_base}/docs/__missing_route_contract__.md"; then
    echo 'Несуществующий docs Markdown не должен падать в Vue SPA fallback.' >&2
    exit 1
fi
docker compose exec -T backend sh -c 'test "$(id -u)" -eq 10001'
docker compose exec -T backend sh -c "skopeo --version | grep -F '1.9.3' >/dev/null"
docker compose exec -T backend sh -c "helm version --short | grep -F 'v3.22.0' >/dev/null"
docker compose exec -T backend python -m alembic -c /app/alembic.ini current --check-heads >/dev/null

if docker compose exec -T frontend env | grep -E '^(HARBOR_|JWT_SECRET=|DATABASE_URL=)' >/dev/null; then
    echo 'Frontend-контейнер неожиданно получил backend-only конфигурацию или секреты.' >&2
    exit 1
fi

docker compose exec -T backend sh -c "printf 'persistent\n' > /app/data/.compose-smoke"
docker compose restart >/dev/null
wait_backend
wait_frontend
docker compose exec -T frontend wget -q -O - "${frontend_container_base}/api/health" | grep -F '"status":"ok"' >/dev/null
docker compose exec -T backend test -f /app/data/.compose-smoke

# Acceptance #5: down/up must preserve state, and the same already-built images
# must start in the opposite contour without any build or image pull.
docker compose down >/dev/null
export PORTAL_CONTOUR="$opposite_contour"
docker compose up -d --no-build --pull never
wait_backend
wait_frontend
docker compose exec -T frontend wget -q -O - "${frontend_container_base}/runtime-config.js" | grep -F "contour: '${opposite_contour}'" >/dev/null
docker compose exec -T frontend wget -q -O - "${frontend_container_base}/api/health" | grep -F '"status":"ok"' >/dev/null
docker compose exec -T backend test -f /app/data/.compose-smoke
docker compose exec -T backend rm /app/data/.compose-smoke

printf '%s\n' 'Compose smoke test пройден: bridge/service discovery, frontend-only host publication, logging policy, миграции до текущего head, proxy/docs health, SOURCE/TARGET, изоляция runtime и persistent volume проверены без повторной сборки/загрузки образов.'
