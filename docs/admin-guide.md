# Руководство администратора Harbor Transfer Portal

**Статус:** актуальная эксплуатационная инструкция для текущего development/runtime Compose v1.

Этот документ описывает администрирование одной установки Harbor Transfer Portal в контуре `SOURCE` или `TARGET`: первичный запуск, локальных пользователей, подключение локального Harbor, credentials/CA, ключи Bundle v1, persistent data, backup/restore и операционные лимиты.

Он **не является финальной инструкцией offline installation kit**. Готовая поставка, upgrade/uninstall workflow и clean-VM acceptance относятся к задаче #28. Нормативный формат переносимого пакета определяется [Offline Bundle Protocol v1](offline-bundle-v1.md), а security/trust model — [security.md](security.md).

## 1. Что администрирует одна установка

Harbor Transfer Portal разворачивается как две независимые установки:

- `SOURCE` — только в исходном контуре и только со своим локальным Harbor;
- `TARGET` — только в целевом контуре и только со своим локальным Harbor.

Одна установка не хранит credentials противоположного Harbor и не должна создавать сетевой путь между контурами.

Роль экземпляра фиксируется в `.env`:

```text
PORTAL_CONTOUR=SOURCE
```

или:

```text
PORTAL_CONTOUR=TARGET
```

Не переключайте рабочую установку SOURCE ↔ TARGET как обычную операционную процедуру. Для разных контуров должны существовать отдельные installation state, secrets, keys и persistent volume.

## 2. Предварительные требования

Для текущего Compose deployment нужны:

- Docker Engine;
- Docker Compose v2;
- доступ администратора к host, где запускается Portal;
- локальный Harbor текущего контура;
- отдельный Harbor service account / технический пользователь;
- для SOURCE — Ed25519 signing key pair;
- для TARGET — заранее полученные trusted SOURCE public keys;
- достаточный disk capacity для SQLite, workspace и переносимых payload.

Текущий `docker compose build` использует внешние build dependencies. В закрытом контуре штатная эксплуатация должна использовать заранее собранные images; финальный offline kit формируется в #28.

Подробности runtime topology: [deploy/README.md](../deploy/README.md).

## 3. Harbor service account и принцип минимальных прав

Используйте отдельный service account, а не персональную учётную запись администратора Harbor.

Минимальный принцип для v1:

- SOURCE требуется чтение metadata и pull/read тех проектов и артефактов, которые разрешено экспортировать;
- TARGET требуется чтение metadata и push/write тех target projects/repositories, куда разрешён импорт;
- Portal не должен получать глобальные Harbor admin privileges только «для удобства»;
- доступ должен быть ограничен локальным Harbor текущего контура.

Точная минимальная Harbor RBAC matrix для feature-specific export/import должна быть повторно проверена при стабилизации #17 и #19. Пока orchestration не завершена, не фиксируйте широкую роль Harbor Administrator как обязательное требование продукта.

## 4. Подготовка `.env`

Создайте конфигурацию из шаблона:

```bash
cp .env.example .env
```

Проверьте как минимум:

```text
PORTAL_CONTOUR=SOURCE|TARGET
PORTAL_HTTP_PORT=8080
HARBOR_URL=https://harbor.local.example
HARBOR_USER=<local-service-account>
HARBOR_VERIFY_TLS=true
JWT_SECRET=<unique-random-secret-at-least-32-chars>
```

Не храните в Git:

- `JWT_SECRET`;
- Harbor password/token;
- SOURCE private signing key;
- приватные ключи TLS;
- backup archive с `data/secrets` или SOURCE private key.

`HARBOR_URL` должен быть безопасным origin без `user:password@host`, query, fragment и произвольного subpath.

### 4.1. Основные пути по умолчанию

```text
DATABASE_URL=sqlite:///./data/harbor-transfer-portal.db
HARBOR_MANAGED_SECRET_FILE=./data/secrets/harbor-password
HARBOR_MANAGED_CA_FILE=./data/secrets/harbor-ca.pem
BUNDLE_SIGNING_PRIVATE_KEY_FILE=./data/keys/source-signing-private.pem
BUNDLE_TRUSTED_PUBLIC_KEYS_DIR=./data/keys/trusted-source
OPERATION_WORKSPACE_ROOT=./data/tmp/operations
```

В контейнере `./data` соответствует `/app/data` persistent volume.

## 5. Первый запуск

Проверьте Compose configuration:

```bash
docker compose config
```

Для текущей development/build среды:

```bash
docker compose up -d --build
```

или через Make:

```bash
make compose-config
make up
```

Портал по умолчанию доступен по адресу:

```text
http://localhost:${PORTAL_HTTP_PORT:-8080}
```

Проверки состояния:

```text
GET /api/health
GET /api/ready
GET /healthz
```

Backend entrypoint применяет Alembic migrations до запуска API. Если migration завершается ошибкой, backend не должен считаться готовым.

## 6. Первичный local admin

После первого запуска создайте bootstrap administrator.

Пароль передавайте только через временную environment variable процесса команды:

```bash
export BOOTSTRAP_ADMIN_PASSWORD='replace-with-a-strong-password'
docker compose exec -T \
  -e BOOTSTRAP_ADMIN_PASSWORD="$BOOTSTRAP_ADMIN_PASSWORD" \
  backend python -m app.auth.cli --username admin
unset BOOTSTRAP_ADMIN_PASSWORD
```

Команда идемпотентна: существующий bootstrap admin автоматически не получает новый пароль повторным запуском команды.

После создания администратора войдите через web UI.

Если API возвращает `authentication is not configured`, проверьте наличие валидного `JWT_SECRET` и перезапустите backend с корректной конфигурацией.

## 7. Локальные пользователи и роли

Текущие роли:

| Роль | Назначение |
|---|---|
| `admin` | настройки Portal/Harbor, управление пользователями, административная отмена операций |
| `operator` | рабочие transfer-действия и отмена собственных операций, когда соответствующий flow реализован |
| `viewer` | read-only доступ к разрешённой информации |

Backend содержит admin-only API:

```text
GET   /api/users
POST  /api/users
PATCH /api/users/{user_id}
```

Создание пользователя требует:

- username — 1…128 символов, нормализуется в lowercase/trim;
- password — минимум 12 символов;
- role — `admin`, `operator` или `viewer`.

`PATCH` позволяет изменить role, `is_active` и password.

### Управление через web UI

После входа под `admin` откройте раздел **«Пользователи»** (`/users`). Экран показывает username, role, active status, дату создания и время последнего успешного входа.

Через UI можно:

- создать локального пользователя с начальным password и role;
- изменить role;
- активировать или деактивировать учётную запись;
- задать новый password;
- обновить список пользователей.

Security-sensitive изменения role/status/password требуют явного подтверждения. Password и password hash не возвращаются API и не отображаются после сохранения.

Backend не позволяет деактивировать или понизить роль единственного активного `admin`: такая попытка завершается `409`. Сначала создайте или активируйте второго администратора, затем изменяйте первого.

Hard-delete локальных пользователей не используется: persisted operation/audit records должны сохранять понятную identity history. Создание и изменение пользователей записываются в audit как `user.created`/`user.updated` с target identifiers и именами изменённых полей, но без password/password hash.

Подробный current contract и операционная процедура: [Управление локальными пользователями](admin-user-management.md).

Не помещайте реальные login passwords или bearer tokens в issue, PR, shell history, shared screenshots или постоянные script files.

## 8. Настройка локального Harbor через UI

После входа под `admin` откройте **«Настройки локального Harbor»**.

Доступны:

- URL локального Harbor;
- username/service account;
- TLS verification;
- установка/rotation credential;
- загрузка/removal custom CA;
- connection test.

Изменения effective Harbor settings применяются без restart backend.

### 8.1. Credential

Предпочтительный persistent path — portal-managed file-backed secret:

```text
HARBOR_MANAGED_SECRET_FILE=./data/secrets/harbor-password
```

Managed secret создаётся server-side с restrictive permissions и находится в persistent volume. Значение не возвращается обратно в UI/API после сохранения.

Fallback order:

1. managed credential file;
2. `HARBOR_PASSWORD_FILE`;
3. `HARBOR_PASSWORD` environment.

`HARBOR_PASSWORD` поддерживается для bootstrap compatibility, но не является рекомендуемым постоянным storage для новой установки.

### 8.2. Rotation Harbor credential

Безопасная последовательность:

1. создайте/смените credential в локальном Harbor;
2. войдите в Portal как `admin`;
3. в Settings установите новый password/token;
4. выполните **«Проверить подключение»**;
5. после успешной проверки отзовите старый credential в Harbor, если использовалось overlap-окно.

Portal audit metadata фиксирует факт изменения, но не старое/новое значение секрета.

## 9. TLS и private CA

Штатное значение:

```text
HARBOR_VERIFY_TLS=true
```

При private PKI:

1. оставьте TLS verification включённой;
2. в admin Settings загрузите PEM/CRT CA bundle;
3. выполните connection test;
4. проверьте, что connection test успешен без отключения TLS verification.

Managed CA хранится в:

```text
HARBOR_MANAGED_CA_FILE=./data/secrets/harbor-ca.pem
```

Deployment-managed fallback `HARBOR_CA_FILE` допустим только если deployment действительно монтирует этот файл в backend.

`HARBOR_VERIFY_TLS=false` не является штатным способом исправления x509/private-CA ошибки.

## 10. SOURCE: signing key lifecycle

SOURCE создаёт Bundle v1 и поэтому хранит Ed25519 private key.

Сгенерируйте key pair на административной машине:

```bash
umask 077
openssl genpkey -algorithm ED25519 -out source-signing-private.pem
openssl pkey -in source-signing-private.pem -pubout -out source-signing-public.pem
```

Private key остаётся только на SOURCE.

Передать private key в current Compose persistent volume можно через stdin:

```bash
docker compose exec -T backend sh -c '
  umask 077
  mkdir -p /app/data/keys
  cat > /app/data/keys/source-signing-private.pem
' < source-signing-private.pem
```

Не передавайте private key:

- внутри Offline Bundle;
- через TARGET;
- в command-line argument;
- в Git;
- в application log;
- через issue/PR/chat text.

Проверяйте, что файл доступен только backend user и имеет restrictive permissions, штатно `0600`.

## 11. TARGET: trusted SOURCE public keys

TARGET хранит только доверенные public keys SOURCE:

```text
BUNDLE_TRUSTED_PUBLIC_KEYS_DIR=./data/keys/trusted-source
```

Пример установки public key:

```bash
docker compose exec -T backend sh -c '
  umask 077
  mkdir -p /app/data/keys/trusted-source
  cat > /app/data/keys/trusted-source/source-2026.pem
' < source-signing-public.pem
```

Public key должен поступать по доверенному организационному каналу, отдельно от обычного доверия к самому переносимому bundle.

### 11.1. Rotation signing key

Используйте overlap:

1. создайте новую SOURCE key pair;
2. заранее добавьте новый public key в TARGET trust set, не удаляя старый;
3. переключите SOURCE на новый private key;
4. выдержите окно, в котором ещё могут прибывать bundle со старой подписью;
5. удалите старый TARGET public key только после завершения этого окна.

TARGET verifier поддерживает несколько trusted `*.pem`.

## 12. Persistent data layout

Compose named volume `portal-data` монтируется в:

```text
/app/data
```

Основные классы данных:

```text
/app/data/
├── harbor-transfer-portal.db
├── packages/
├── incoming/
├── outgoing/
├── logs/
├── receipts/
├── secrets/
├── keys/
└── tmp/
```

Часть каталогов создаётся только при использовании соответствующей функции.

Backend image работает под UID/GID `10001` (`htp`). Runtime files, которые приложение должно менять, должны оставаться доступны этому пользователю.

Не используйте:

```bash
docker compose down -v
```

как обычный restart/update step: эта команда удаляет named volume.

## 13. Backup: что обязательно сохранять

Полный backup установки — не только SQLite.

Минимальный защищаемый набор:

- `/app/data/harbor-transfer-portal.db`;
- `/app/data/secrets/`;
- SOURCE: `/app/data/keys/source-signing-private.pem`;
- TARGET: `/app/data/keys/trusted-source/`;
- необходимые receipts/history metadata;
- configuration, достаточная для восстановления роли установки;
- retained incoming/outgoing packages — только если этого требует retention policy.

Backup SOURCE с private signing key является чувствительным secret backup. Не включайте его в release archive и не храните рядом с публичным SOURCE public key как обычный несекретный artifact.

## 14. Consistent backup текущего Compose

Ниже — baseline для текущего Compose, а не финальный #28 disaster-recovery contract.

### 14.1. Остановить запись

Для согласованного snapshot остановите сервисы:

```bash
docker compose stop frontend backend
```

### 14.2. Снять archive `/app/data`

Создайте защищённый каталог backup на host и сохраните volume через одноразовый container того же backend image:

```bash
umask 077
mkdir -p backup

docker compose run --rm -T --no-deps \
  --entrypoint tar \
  backend -C /app/data -czf - . \
  > "backup/portal-data-$(date +%Y%m%d-%H%M%S).tar.gz"
```

Archive содержит secrets/keys и должен храниться по политике секретных backup.

### 14.3. Вернуть сервисы

```bash
docker compose up -d
```

После backup проверьте health/readiness.

## 15. Restore текущего Compose

Restore выполняйте только в ожидаемую установку с правильным `PORTAL_CONTOUR` и после проверки источника backup.

### 15.1. Остановить сервисы

```bash
docker compose stop frontend backend
```

### 15.2. Очистить восстанавливаемый volume

Это destructive step. Выполняйте его только после подтверждения, что выбран правильный deployment:

```bash
docker compose run --rm -T --no-deps \
  --user root \
  --entrypoint sh \
  backend -c 'find /app/data -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +'
```

### 15.3. Восстановить archive

```bash
cat backup/portal-data-YYYYMMDD-HHMMSS.tar.gz | \
  docker compose run --rm -T --no-deps \
    --user root \
    --entrypoint tar \
    backend -C /app/data -xzf -
```

Верните ownership штатному backend user:

```bash
docker compose run --rm -T --no-deps \
  --user root \
  --entrypoint chown \
  backend -R 10001:10001 /app/data
```

Проверьте restrictive permissions secret/private-key files перед стартом.

### 15.4. Запустить и проверить

```bash
docker compose up -d
```

Проверьте:

- `/api/health`;
- `/api/ready`;
- вход local admin;
- Harbor connection test;
- SOURCE signing key или TARGET trust set;
- Alembic migration state через штатный startup/smoke path.

Если restore выполняется между разными версиями приложения, сначала оцените DB migration compatibility. Автоматический downgrade migrations не считается гарантированным rollback-механизмом.

## 16. Upgrade текущего development/runtime Compose

До появления финального offline release #28 используйте консервативную последовательность:

1. сделайте consistent backup `/app/data`;
2. сохраните копию рабочей `.env` отдельно от репозитория;
3. сравните текущую `.env.example` с установленной конфигурацией;
4. получите/соберите новую версию images только в разрешённой build/release среде;
5. запустите новый backend — Alembic применит forward migrations до API startup;
6. выполните smoke/health checks;
7. проверьте Harbor credential/CA и SOURCE/TARGET key material.

Для development build:

```bash
docker compose up -d --build
```

В air-gap production это не должно превращаться в online build. Там требуются заранее доставленные images/offline kit.

Не обещайте rollback только заменой image tag: если новая версия уже изменила SQLite schema, возврат старого application image может потребовать восстановление pre-upgrade backup.

## 17. Disk capacity и package limits

Основные Bundle limits задаются через `.env`:

- `BUNDLE_MAX_ARCHIVE_BYTES`;
- `BUNDLE_MAX_EXTRACTED_BYTES`;
- `BUNDLE_MAX_MEMBER_COUNT`;
- `BUNDLE_MAX_PATH_BYTES`;
- `BUNDLE_MAX_METADATA_BYTES`;
- `BUNDLE_MAX_COMPRESSION_RATIO`;
- `BUNDLE_MAX_TRUSTED_KEYS`.

OperationManager использует:

```text
OPERATION_MAX_CONCURRENT=2
OPERATION_DISK_RESERVE_BYTES=536870912
OPERATION_SHUTDOWN_TIMEOUT_SECONDS=10
```

Не увеличивайте limits только ради прохождения неожиданно большого/malformed bundle. Перед изменением оцените:

- размер исходных payload;
- одновременное существование source copy, temporary workspace и final bundle;
- TARGET extracted workspace;
- число параллельных операций;
- обязательный disk reserve;
- backup/retention объём.

Baseline v1 использует один backend instance и in-process `asyncio` OperationManager; Redis/Celery не используются.

## 18. Restart, shutdown и фоновые операции

Resume середины Skopeo/Helm-команды после restart в v1 не поддерживается.

При неожиданном restart уже захваченная non-terminal operation, кроме `READY`, переводится в безопасный `FAILED` (`operation_interrupted_restart`). При штатном shutdown аналогичная операция завершается `operation_interrupted_shutdown`.

`READY` является единственным waiting state, для которого stale worker ownership освобождается с сохранением подготовленного workspace.

Поэтому перед planned maintenance:

- по возможности не начинайте новые длительные операции;
- дождитесь terminal state текущих операций;
- не считайте restart способом «продолжить с середины» transfer command.

Подробности: [operation-manager.md](operation-manager.md).

## 19. Логи и audit

Текущий application/runtime log доступен через Docker Compose:

```bash
docker compose logs backend
docker compose logs frontend
```

Для наблюдения в реальном времени:

```bash
docker compose logs -f backend frontend
```

Не публикуйте полные logs без проверки redaction и внутренней metadata.

Security-sensitive administrative changes Harbor settings/credential/CA создают `AuditEvent` в SQLite. Audit metadata содержит только безопасное описание изменённых полей; credential и содержимое CA туда не должны попадать.

Каталог `/app/data/logs` зарезервирован в persistent layout, но наличие каталога не означает, что current backend автоматически пишет туда весь stdout/stderr. Источником истины для current Compose logs остаётся container logging, пока отдельная file-log policy не реализована и не документирована.

## 20. Retention

Финальная retention policy для incoming/outgoing bundles, receipts и history должна определяться организационной политикой и завершёнными #17/#19/#23 flows.

До этого:

- не удаляйте `data/secrets` и key material как «временные файлы»;
- не очищайте `READY` workspace вручную без понимания operation state;
- не смешивайте cleanup transfer payload с backup cleanup;
- не храните bundle бесконечно только потому, что каталог persistent.

Автоматическая product retention/cleanup не должна считаться реализованной без соответствующего кода и tests.

## 21. Проверка после установки или изменения конфигурации

Минимальный административный checklist:

1. `docker compose config` проходит;
2. backend/frontend healthy;
3. `/api/health` и `/api/ready` отвечают;
4. contour соответствует площадке;
5. local admin может войти;
6. Harbor URL/username корректны;
7. credential configured;
8. TLS verification включена;
9. private CA при необходимости установлен и connection test успешен;
10. SOURCE имеет private signing key **или** TARGET имеет правильный trust set;
11. persistent volume не является ephemeral bind/tmp storage;
12. disk reserve и package limits соответствуют capacity;
13. backup procedure проверена в контролируемой среде;
14. `make docs-check`/CI не показывает рассинхрон документации.

Для repository-level Compose smoke:

```bash
./deploy/smoke-compose.sh
```

Smoke test предназначен прежде всего для development/CI и не заменяет площадочный operational acceptance.

## 22. Что пока не следует считать готовым

На текущем v1 development state не заявляются как завершённые:

- финальный SOURCE export wizard/orchestration;
- финальный TARGET intake/preview/import wizard/orchestration;
- полный history/report UX;
- production-tested retention automation;
- финальный offline installation/upgrade/uninstall kit;
- release-grade restore/rollback acceptance на clean VM.

Admin Guide должен обновляться в той же итерации, когда эти области становятся фактически доступными.

## 23. Связанные документы

- [Карта документации](README.md)
- [Deployment/runtime Compose](../deploy/README.md)
- [Архитектура](architecture.md)
- [Security/trust model](security.md)
- [OperationManager](operation-manager.md)
- [Package service и key model](package-service.md)
- [Offline Bundle Protocol v1](offline-bundle-v1.md)
- [Testing/CI](testing.md)
- [Troubleshooting task #61](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/61) — до появления `docs/troubleshooting.md`.

Если этот документ расходится с current code, `.env.example`, accepted ADR или `deploy/README.md`, расхождение является documentation defect и должно исправляться вместе с соответствующим изменением.