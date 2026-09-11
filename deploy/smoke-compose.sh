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
        if docker compose exec -T frontend wget -q -O /dev/null http://127.0.0.1/healthz >/dev/null 2>&1; then
            return 0
        fi
        attempts=$((attempts + 1))
        sleep 2
    done
    echo 'Frontend не перешёл в healthy-состояние.' >&2
    return 1
}

docker compose config >/dev/null
docker compose up -d --build
wait_backend
wait_frontend

docker compose exec -T frontend wget -q -O - http://127.0.0.1/api/health | grep -F '"status":"ok"' >/dev/null
docker compose exec -T frontend wget -q -O - http://127.0.0.1/runtime-config.js | grep -F "contour: '${current_contour}'" >/dev/null
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
docker compose exec -T frontend wget -q -O - http://127.0.0.1/api/health | grep -F '"status":"ok"' >/dev/null
docker compose exec -T backend test -f /app/data/.compose-smoke

# Acceptance #5: down/up must preserve state, and the same already-built images
# must start in the opposite contour without any build or image pull.
docker compose down >/dev/null
export PORTAL_CONTOUR="$opposite_contour"
docker compose up -d --no-build --pull never
wait_backend
wait_frontend
docker compose exec -T frontend wget -q -O - http://127.0.0.1/runtime-config.js | grep -F "contour: '${opposite_contour}'" >/dev/null
docker compose exec -T frontend wget -q -O - http://127.0.0.1/api/health | grep -F '"status":"ok"' >/dev/null
docker compose exec -T backend test -f /app/data/.compose-smoke
docker compose exec -T backend rm /app/data/.compose-smoke

printf '%s\n' 'Compose smoke test пройден: миграции до текущего head, proxy health, SOURCE/TARGET, изоляция runtime и persistent volume проверены без повторной сборки/загрузки образов.'
