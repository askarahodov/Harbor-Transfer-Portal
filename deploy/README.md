# Развертывание через Docker Compose

Этот каталог описывает одиночную установку Harbor Transfer Portal в контуре `SOURCE` или `TARGET`. Одна и та же пара образов используется в обоих контурах; различается только локальная конфигурация установки.

## Runtime-модель

Одна установка содержит два сервиса:

- `backend` — FastAPI, Skopeo и Helm; доступен только во внутренней сети Compose;
- `frontend` — Nginx со собранным SPA и reverse proxy `/api/` на backend.

Хостовый HTTP-порт публикует только frontend. Браузер обращается к API same-origin через Nginx, поэтому CORS в штатной Compose-топологии не требуется.

Каждый backend получает настройки и учётные данные **только своего локального Harbor**. Конфигурация Harbor, `DATABASE_URL` и `JWT_SECRET` во frontend-контейнер не передаются.

## Подготовка конфигурации

Создайте локальный `.env`:

```bash
cp .env.example .env
```

Перед запуском обязательно проверьте как минимум:

- `PORTAL_CONTOUR=SOURCE` или `PORTAL_CONTOUR=TARGET`;
- `HARBOR_URL`, `HARBOR_USER`, `HARBOR_PASSWORD` для локального Harbor;
- `HARBOR_VERIFY_TLS=true` в штатной конфигурации;
- уникальный `JWT_SECRET` длиной не менее 32 случайных символов;
- `DATABASE_URL`, если используется путь, отличный от стандартного SQLite в `/app/data`.

`.env` исключён из Git и Docker build context. Не коммитьте реальные пароли, JWT secrets, приватные ключи и закрытые сертификаты.

### Частный CA Harbor

При частной PKI задайте `HARBOR_CA_FILE` как путь, доступный backend-контейнеру. Для Compose можно хранить доверенный сертификат в persistent volume, например `/app/data/harbor-ca.crt`. Сертификат должен быть помещён туда до первого обращения приложения к Harbor. Отключение `HARBOR_VERIFY_TLS` допустимо только как явное исключение для диагностики и оставляет warning в логах.

## Persistent data и миграции

Named volume `portal-data` монтируется в `/app/data`. В нём сохраняются база данных и рабочие области портала. Образ заранее создаёт каталоги:

- `/app/data/database` — резерв для DB-related данных;
- `/app/data/packages` — контролируемая рабочая область пакетов;
- `/app/data/incoming` — входящие пакеты TARGET;
- `/app/data/outgoing` — готовые пакеты SOURCE;
- `/app/data/logs` — постоянные логи, когда их запись включена;
- `/app/data/receipts` — отчёты и receipts импорта;
- `/app/data/tmp` — временные данные операций.

Стандартный `DATABASE_URL=sqlite:///./data/harbor-transfer-portal.db` указывает на файл `/app/data/harbor-transfer-portal.db` внутри persistent volume.

Перед каждым запуском backend entrypoint выполняет:

```text
python -m alembic -c /app/alembic.ini upgrade head
```

Alembic использует `DATABASE_URL` из окружения, если он задан. Uvicorn запускается только после успешного применения миграций; при ошибке миграции backend не начинает обслуживать API.

`docker compose down` сохраняет named volume. Команда `docker compose down -v` удаляет его вместе с постоянными данными и не должна использоваться, если данные требуется сохранить.

## Запуск

Проверьте конфигурацию и запустите стек:

```bash
docker compose config
docker compose up -d --build
```

Или используйте Make targets:

```bash
make compose-config
make up
```

Портал доступен на `http://localhost:${PORTAL_HTTP_PORT:-8080}`. Backend health через reverse proxy: `/api/health`; собственный health endpoint Nginx: `/healthz`.

## Первичный администратор

После первого запуска создайте администратора через локальный CLI. Пароль передаётся только через переменную окружения `BOOTSTRAP_ADMIN_PASSWORD` и не сохраняется в `.env.example`:

```bash
export BOOTSTRAP_ADMIN_PASSWORD='replace-with-a-strong-password'
docker compose exec -T \
  -e BOOTSTRAP_ADMIN_PASSWORD="$BOOTSTRAP_ADMIN_PASSWORD" \
  backend python -m app.auth.cli --username admin
unset BOOTSTRAP_ADMIN_PASSWORD
```

Команда идемпотентна: если bootstrap-admin уже существует, его пароль автоматически не перезаписывается.

## Build-time зависимости и offline runtime

Во время **сборки** образов требуется доступ к внешним источникам:

- `python:3.12.14-slim-bookworm`;
- `node:22.23.2-alpine3.24`;
- `nginx:1.30.1-alpine`;
- Debian bookworm repositories для `skopeo=1.9.3+ds1-1+b10`, CA certificates, tar и gzip;
- Python package index для `backend/pyproject.toml`;
- npm registry для frontend dependencies;
- `get.helm.sh` для Helm `v3.22.0` только на стадии build.

Helm archive проверяется по architecture-specific SHA256 до установки. Backend foundation поддерживает сборку `linux/amd64` и `linux/arm64`.

После сборки или загрузки готовых образов обычный `docker compose up -d`/restart не скачивает runtime-зависимости из интернета. Финальная offline-поставка должна распространять заранее собранные образы, а не повторять online build в закрытом контуре.

## Проверка развертывания

Scoped smoke test:

```bash
./deploy/smoke-compose.sh
```

Он проверяет:

- корректность `docker compose config`;
- сборку и healthy-состояние обоих сервисов;
- `/api/` proxy и runtime contour config;
- применение Alembic migration `0001_initial`;
- запуск backend под UID `10001`, а не root;
- ожидаемые версии Skopeo и Helm;
- отсутствие `HARBOR_*`, `JWT_SECRET` и `DATABASE_URL` во frontend environment;
- сохранение marker-файла после `docker compose restart`;
- сохранение того же marker-файла после `docker compose down` и повторного `up`;
- запуск **тех же уже собранных образов** в противоположном `SOURCE`/`TARGET` contour через `--no-build --pull never`.

Последний этап одновременно подтверждает, что штатный повторный runtime start не требует build или загрузки образов из сети. Smoke test останавливает контейнеры при завершении, но сохраняет `portal-data`.
