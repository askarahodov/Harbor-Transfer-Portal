#!/usr/bin/env sh
set -eu

cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

if [ ! -f .env ]; then
    cp .env.example .env
    chmod 600 .env
    printf '%s\n' '.env создан из шаблона. JWT_SECRET сгенерирует backend и сохранит в volume.'
fi

docker compose up -d --build --force-recreate
printf '%s\n' 'Portal: http://127.0.0.1:8080/'
printf '%s\n' 'На новой базе войдите admin/admin и обязательно смените пароль.'
