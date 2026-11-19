#!/usr/bin/env sh
set -eu

cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

fail() {
    printf '%s\n' "ERROR: $*" >&2
    exit 1
}

command -v docker >/dev/null 2>&1 || fail "Docker не найден. Установите Docker Engine и Compose v2."
docker info >/dev/null 2>&1 || fail "Docker Engine недоступен. Запустите Docker и проверьте права пользователя."
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 недоступен. Установите Compose plugin."
docker_os="$(docker info --format '{{.OSType}}' 2>/dev/null)" || fail "Не удалось определить Docker OSType."
[ "$docker_os" = linux ] || fail "Нужен Docker в режиме Linux containers (текущий OSType: $docker_os)."

if [ ! -f .env ]; then
    [ -f .env.example ] || fail "Не найден .env.example."
    cp .env.example .env
    chmod 600 .env
    printf '%s\n' '.env создан из шаблона. JWT_SECRET сгенерирует backend и сохранит в volume.'
fi

revision="$(git rev-parse --verify HEAD 2>/dev/null)" || fail "Не удалось определить Git commit. Запустите из клона опубликованного main."
export PORTAL_IMAGE_REVISION="$revision"

docker compose -f compose.online.yaml config -q || fail "Ошибка конфигурации Compose или .env."
docker compose -f compose.online.yaml pull --policy always || fail "Образы недоступны в GHCR: проверьте публикацию этого Git commit, сеть и доступ (docker login ghcr.io)."
if ! docker compose -f compose.online.yaml up -d --no-build --pull never --wait --wait-timeout 180; then
    printf '%s\n' 'Запуск не завершён: проверьте docker compose -f compose.online.yaml ps и docker compose -f compose.online.yaml logs --tail=100.' >&2
    exit 1
fi

printf '%s\n' 'Portal готов из готовых GHCR образов. Опубликованный адрес (host:port):'
docker compose -f compose.online.yaml port frontend 8080 || fail "Не удалось определить опубликованный порт."
printf '%s\n' 'На новой базе войдите admin/admin и обязательно смените пароль.'
