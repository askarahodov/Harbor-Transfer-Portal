# Развертывание через Docker Compose

**Статус:** документация текущего development/runtime Compose для Harbor Transfer Portal v1.

Этот документ описывает одиночную установку в контуре `SOURCE` или `TARGET`. Он **не является финальной инструкцией offline installation kit**: готовый поставочный archive, install/upgrade/uninstall workflow и clean-VM acceptance относятся к задаче #28.

## 1. Runtime-модель

Одна установка содержит два сервиса:

- `backend` — FastAPI, SQLite access, Skopeo, Helm и transfer services; доступен только во внутренней Compose network;
- `frontend` — Nginx со собранным Vue SPA и reverse proxy `/api/` на backend.

Хостовый HTTP-порт публикует только frontend. Браузер обращается к API same-origin через Nginx.

Каждый backend получает настройки и credentials **только своего локального Harbor**. Конфигурация Harbor, `DATABASE_URL`, `JWT_SECRET`, signing private key и managed secrets во frontend-контейнер не передаются.

Обе роли используют одни и те же application images. Роль экземпляра задаётся:

```text
PORTAL_CONTOUR=SOURCE
```

или:

```text
PORTAL_CONTOUR=TARGET
```

SOURCE и TARGET не настраиваются одновременно в одном экземпляре.

## 2. Важное различие: development Compose и offline release

Текущий `docker compose build` может обращаться к внешним источникам build dependencies. Это допустимо только в контролируемой build/release среде.

В закрытом контуре штатный runtime должен запускаться из **заранее собранных и доставленных images**. Финальный offline release не должен требовать PyPI/npm/apt/get.helm.sh для установки на закрытой площадке.

Поэтому:

- для разработки используется текущий Compose build;
- для финального air-gap deployment задача #28 должна сформировать prebuilt offline kit;
- успешный development `docker compose up -d --build` сам по себе не означает готовность production offline installer.

## 3. Persistent volume

Named volume `portal-data` монтируется backend в `/app/data`.

Текущая структура runtime данных включает:

```text
/app/data/
├── harbor-transfer-portal.db
├── database/
├── packages/
├── incoming/
├── outgoing/
├── logs/
├── receipts/
├── secrets/
├── keys/
└── tmp/
```

Часть каталогов создаётся по мере использования соответствующих функций.

Backend image работает не от root, а под UID/GID `10001` (`htp`). Все runtime-файлы, которые приложение должно изменять, должны быть доступны этому пользователю.

`docker compose down` сохраняет named volume. Команда:

```bash
docker compose down -v
```

удаляет volume вместе с постоянными данными и не должна использоваться, если данные требуется сохранить.

## 4. Подготовка `.env`

Создайте локальный файл конфигурации:

```bash
cp .env.example .env
```

`.env` исключён из Git и Docker build context.

Перед первым запуском проверьте как минимум:

- `PORTAL_CONTOUR=SOURCE` или `PORTAL_CONTOUR=TARGET`;
- `PORTAL_HTTP_PORT` при необходимости;
- `HARBOR_URL` для локального Harbor;
- `HARBOR_USER` для local service account / пользователя;
- `HARBOR_VERIFY_TLS=true` в штатной конфигурации;
- уникальный `JWT_SECRET` длиной не менее 32 случайных символов;
- `DATABASE_URL`, только если используется путь, отличный от стандартного SQLite;
- security/resource limits, если политика установки требует отличий от `.env.example`.

Не коммитьте реальные passwords, tokens, `JWT_SECRET`, private signing keys или закрытые сертификаты.

## 5. Harbor configuration: bootstrap и managed state

Текущая модель следует [ADR-005](../docs/adr/ADR-005-harbor-secrets-tls.md).

### 5.1. Non-secret bootstrap

Для первого запуска достаточно задать non-secret connection metadata:

```text
HARBOR_URL=https://harbor.local.example
HARBOR_USER=replace-with-local-harbor-user
HARBOR_VERIFY_TLS=true
```

После создания локального admin предпочтительный путь — сохранить/изменить Harbor settings через web UI `Настройки локального Harbor`.

UI поддерживает:

- URL;
- username/service account;
- TLS verification flag;
- отдельную установку/rotation Harbor credential;
- загрузку/removal custom CA;
- проверку соединения.

Изменения effective settings применяются без restart backend.

### 5.2. Harbor credential: предпочтительный managed flow

В новой установке не рекомендуется хранить Harbor password как постоянное значение в `.env`.

При установке/rotation через admin UI backend записывает credential в file-backed managed secret:

```text
HARBOR_MANAGED_SECRET_FILE=./data/secrets/harbor-password
```

Файл создаётся с restrictive permissions и находится в persistent volume. Значение не возвращается обратно в API/UI после сохранения.

### 5.3. Bootstrap credential fallbacks

Backend поддерживает следующий fallback order:

1. managed credential file;
2. `HARBOR_PASSWORD_FILE`;
3. `HARBOR_PASSWORD` environment.

`HARBOR_PASSWORD_FILE` подходит для deployment-managed secret, если конкретная Compose/оркестрационная конфигурация **явно монтирует** такой файл в backend. Базовый `compose.yaml` сам по себе отдельный secret mount не создаёт.

`HARBOR_PASSWORD` остаётся compatibility/bootstrap fallback, но не является предпочтительным постоянным production storage.

## 6. TLS и custom CA

TLS verification должна оставаться включённой:

```text
HARBOR_VERIFY_TLS=true
```

При private PKI предпочтительный путь после первого входа:

1. открыть admin Settings;
2. загрузить PEM/CRT CA bundle;
3. выполнить `Проверить подключение`;
4. убедиться, что TLS verification остаётся включённой.

Portal-managed CA хранится в:

```text
HARBOR_MANAGED_CA_FILE=./data/secrets/harbor-ca.pem
```

Deployment fallback `HARBOR_CA_FILE` также поддерживается, если deployment самостоятельно монтирует файл в backend.

`HARBOR_VERIFY_TLS=false` не является штатным исправлением x509/private CA проблемы. Silent fallback на insecure TLS отсутствует.

## 7. Запуск Compose

Проверьте итоговую конфигурацию:

```bash
docker compose config
```

Для development build/start:

```bash
docker compose up -d --build
```

или через Make:

```bash
make compose-config
make up
```

Портал доступен на:

```text
http://localhost:${PORTAL_HTTP_PORT:-8080}
```

Backend health через reverse proxy:

```text
/api/health
```

Nginx health:

```text
/healthz
```

## 8. Миграции

Перед запуском Uvicorn backend entrypoint выполняет:

```text
python -m alembic -c /app/alembic.ini upgrade head
```

Alembic использует `DATABASE_URL` из environment, если он задан. При ошибке migration backend не начинает обслуживать API.

Smoke test дополнительно проверяет migration heads через Alembic.

Стандартный:

```text
DATABASE_URL=sqlite:///./data/harbor-transfer-portal.db
```

соответствует `/app/data/harbor-transfer-portal.db` внутри persistent volume.

## 9. Первичный администратор

После первого запуска создайте local bootstrap admin через CLI.

Пароль передаётся только через временную environment variable процесса команды и не должен записываться в `.env.example`:

```bash
export BOOTSTRAP_ADMIN_PASSWORD='replace-with-a-strong-password'
docker compose exec -T \
  -e BOOTSTRAP_ADMIN_PASSWORD="$BOOTSTRAP_ADMIN_PASSWORD" \
  backend python -m app.auth.cli --username admin
unset BOOTSTRAP_ADMIN_PASSWORD
```

Команда идемпотентна: существующему bootstrap admin пароль автоматически не перезаписывается.

После входа под admin настройте local Harbor через Settings и выполните connection test.

## 10. SOURCE: signing private key

SOURCE требует Ed25519 private signing key для создания Bundle v1.

Ключ генерируется **на административной машине**, а не внутри delivery bundle. Пример из package service documentation:

```bash
umask 077
openssl genpkey -algorithm ED25519 -out source-signing-private.pem
openssl pkey -in source-signing-private.pem -pubout -out source-signing-public.pem
```

Private key должен остаться только в SOURCE.

Стандартный backend path:

```text
BUNDLE_SIGNING_PRIVATE_KEY_FILE=./data/keys/source-signing-private.pem
```

Для текущего Compose private key можно безопасно передать через stdin в persistent volume от имени штатного container user:

```bash
docker compose exec -T backend sh -c '
  umask 077
  mkdir -p /app/data/keys
  cat > /app/data/keys/source-signing-private.pem
' < source-signing-private.pem
```

Не выводите private key в shell arguments, logs или PR/issue text.

Публичную половину key pair передайте на TARGET доверенным организационным способом отдельно от private key.

## 11. TARGET: trusted SOURCE public keys

TARGET не получает SOURCE private key. Он хранит только trusted SOURCE public keys.

Стандартный trust directory:

```text
BUNDLE_TRUSTED_PUBLIC_KEYS_DIR=./data/keys/trusted-source
```

Установить public key в текущий Compose можно так:

```bash
docker compose exec -T backend sh -c '
  umask 077
  mkdir -p /app/data/keys/trusted-source
  cat > /app/data/keys/trusted-source/source-2026.pem
' < source-signing-public.pem
```

Verifier поддерживает несколько `*.pem`, что позволяет выполнить key rotation с overlap.

Порядок rotation:

1. создать новую key pair на SOURCE;
2. заранее установить новый public key на TARGET, сохранив старый;
3. переключить SOURCE на новый private key;
4. выдержать операционное окно старых delivery;
5. затем удалить старый public key из trust set.

Подробности: [package-service.md](../docs/package-service.md) и [security.md](../docs/security.md).

## 12. Bundle/runtime limits

`.env.example` содержит server-side limits, в том числе:

- `BUNDLE_MAX_ARCHIVE_BYTES`;
- `BUNDLE_MAX_EXTRACTED_BYTES`;
- `BUNDLE_MAX_MEMBER_COUNT`;
- `BUNDLE_MAX_PATH_BYTES`;
- `BUNDLE_MAX_METADATA_BYTES`;
- `BUNDLE_MAX_COMPRESSION_RATIO`;
- `BUNDLE_MAX_TRUSTED_KEYS`;
- Skopeo/Helm timeout и retained output limits.

Не увеличивайте лимиты только для того, чтобы malformed/неожиданно большой bundle прошёл verification. Изменение лимитов требует оценки disk/capacity/security impact.

## 13. Build-time dependencies и offline runtime

Текущий backend/frontend build использует внешние источники, включая:

- `python:3.12.14-slim-bookworm`;
- `node:22.23.2-alpine3.24`;
- `nginx:1.30.1-alpine`;
- Debian bookworm repositories для Skopeo/CA/tar/gzip;
- Python package index;
- npm registry;
- `get.helm.sh` для Helm `v3.22.0` на build stage.

Helm archive проверяется по architecture-specific SHA-256 до установки. Backend image поддерживает `linux/amd64` и `linux/arm64` в рамках текущего Dockerfile.

После сборки или загрузки готовых images обычный restart не должен загружать runtime dependencies из интернета.

Финальная offline-поставка должна распространять prebuilt images и проверяемый install payload; это будет завершено в #28.

## 14. Проверка развертывания

Scoped smoke test:

```bash
./deploy/smoke-compose.sh
```

Он проверяет:

- `docker compose config`;
- build и healthy state обоих сервисов;
- `/api/` proxy и runtime contour config;
- нахождение DB на текущих Alembic migration heads;
- запуск backend под UID `10001`;
- ожидаемые версии Skopeo и Helm;
- отсутствие `HARBOR_*`, `JWT_SECRET` и `DATABASE_URL` во frontend environment;
- сохранение marker после restart;
- сохранение marker после `docker compose down` и повторного `up`;
- запуск тех же уже собранных images в противоположном SOURCE/TARGET contour через `--no-build --pull never`.

Последний этап подтверждает, что повторный runtime start не требует rebuild/pull.

## 15. Что входит в backup

Полный backup установки — не только SQLite.

Минимально учитывать нужно разные классы данных:

- SQLite database;
- portal-managed `data/secrets`;
- SOURCE signing private key **или** TARGET trusted public keys;
- receipts/history metadata, когда соответствующие функции используются;
- configuration needed to reconstruct installation;
- retained incoming/outgoing packages — только согласно принятой retention policy.

Не помещайте пользовательские secrets/private keys в публичный release archive.

Полная backup/restore/upgrade процедура относится к [admin guide #60](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/60) и release task #28. До их завершения не считайте этот раздел гарантией протестированного disaster-recovery процесса.

## 16. Безопасное удаление/пересоздание

Перед destructive Compose operations определите, требуется ли сохранить `portal-data`.

Не используйте `docker compose down -v` как обычный restart/update step.

При переустановке не удаляйте автоматически:

- SQLite;
- managed Harbor credential/CA;
- signing/trust keys;
- history/receipts, которые должны сохраняться по политике.

## 17. Связанные документы

- [Архитектура](../docs/architecture.md)
- [Security/trust model](../docs/security.md)
- [ADR-005: Harbor secrets/TLS](../docs/adr/ADR-005-harbor-secrets-tls.md)
- [Package service / key model](../docs/package-service.md)
- [Offline Bundle Protocol v1](../docs/offline-bundle-v1.md)
- [Testing/CI](../docs/testing.md)
- [Паспорт проекта](../docs/project-passport.md)

Если deployment guide расходится с current code, `.env.example` или принятым ADR, такое расхождение считается documentation defect и должно исправляться вместе с соответствующей итерацией.