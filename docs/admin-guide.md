# Руководство администратора Harbor Transfer Portal

**Статус:** актуальная эксплуатационная инструкция для текущего development/runtime Compose v1.

Этот документ описывает администрирование одной установки Harbor Transfer Portal в контуре `SOURCE` или `TARGET`: первичный запуск, локальных пользователей, подключение локального Harbor, credentials/CA, transfer policies, ключи Bundle v1, persistent data, backup/restore и операционные лимиты.

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

SOURCE browse/export и TARGET preview/import уже используют эти локальные credentials. Не расширяйте Harbor роль шире фактического project/repository scope только ради устранения отдельного `403`.

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
BUNDLE_KEY_MATERIAL_MAX_BYTES=65536
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
| `admin` | настройки Portal/Harbor, управление пользователями/policies/keys, административная отмена операций |
| `operator` | SOURCE export, TARGET intake/import и отмена собственных операций |
| `viewer` | read-only доступ к разрешённой истории/отчётам и состоянию |

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

### 10.1. Штатная установка/rotation через browser

После входа под `admin` откройте **Settings → Signing и trust keys**. В SOURCE-контуре UI показывает только:

- `configured / not configured`;
- стабильный `sha256:` fingerprint public part.

Выберите PEM Ed25519 private key и подтвердите install/rotation. Backend:

- ограничивает размер через `BUNDLE_KEY_MATERIAL_MAX_BYTES`;
- разбирает и проверяет Ed25519 private key до замены;
- сохраняет нормализованный PKCS#8 PEM атомарно в `BUNDLE_SIGNING_PRIVATE_KEY_FILE`;
- устанавливает restrictive mode `0600`;
- возвращает только action + fingerprint;
- записывает audit без key material.

После отправки private key **нельзя скачать/прочитать обратно через normal API/UI**. Frontend также не подставляет его обратно в form state.

При rotation сначала заранее добавьте новый public key в TARGET trust set, чтобы создать overlap window, и только затем переключайте SOURCE private key.

### 10.2. Deployment/break-glass fallback

Ручная запись файла допустима для bootstrap/recovery, когда browser workflow недоступен, но не является предпочтительным normal lifecycle:

```bash
docker compose exec -T backend sh -c '
  umask 077
  mkdir -p /app/data/keys
  cat > /app/data/keys/source-signing-private.pem
' < source-signing-private.pem
```

После ручной установки проверьте mode `0600` и SOURCE key status/fingerprint через admin Settings.

Не передавайте private key:

- внутри Offline Bundle;
- через TARGET;
- в command-line argument;
- в Git;
- в application log;
- через issue/PR/chat text.

Подробный lifecycle: [key-management.md](key-management.md).

## 11. TARGET: trusted SOURCE public keys

TARGET хранит только доверенные public keys SOURCE:

```text
BUNDLE_TRUSTED_PUBLIC_KEYS_DIR=./data/keys/trusted-source
```

Public key должен поступать по доверенному организационному каналу, отдельно от обычного доверия к самому переносимому bundle.

### 11.1. Штатное управление через browser

В TARGET-контуре **Settings → Signing и trust keys** позволяет администратору:

- видеть trusted keys по стабильному `sha256:` fingerprint;
- добавить/заменить Ed25519 public key;
- включить или отключить trust;
- удалить key;
- видеть active/disabled state.

Каждая security-sensitive mutation требует явного подтверждения. Backend принимает только валидный Ed25519 **public** key: private/malformed/oversized material отклоняется. Имена файлов создаются server-side из fingerprint; пользователь не передаёт filesystem path.

Enabled keys представлены `*.pem` и потребляются тем же `BundlePackageService.verify_bundle()` path. Disabled key физически исключается из active `*.pem` set, поэтому выключение trust реально влияет на verifier.

### 11.2. Rotation signing key

Используйте overlap:

1. создайте новую SOURCE key pair;
2. заранее добавьте новый public key в TARGET trust set, не удаляя старый;
3. убедитесь, что оба fingerprints active;
4. переключите SOURCE на новый private key;
5. выдержите окно, в котором ещё могут прибывать bundle со старой подписью;
6. сначала disable старый TARGET public key и наблюдайте expected flow;
7. удалите старый key только после завершения migration window.

TARGET verifier поддерживает несколько одновременно enabled trusted keys.

### 11.3. Deployment/break-glass fallback

Legacy/manual `*.pem` в trust directory остаются совместимыми. Например:

```bash
docker compose exec -T backend sh -c '
  umask 077
  mkdir -p /app/data/keys/trusted-source
  cat > /app/data/keys/trusted-source/source-2026.pem
' < source-signing-public.pem
```

Managed mutation канонизирует соответствующий legacy key по fingerprint. После ручной установки проверьте trust list через UI.

Подробный contract: [key-management.md](key-management.md).

## 12. Transfer policies и limits через UI

В admin Settings доступны поддерживаемые runtime policies:

- `import_allow_overwrite` — глобально разрешает отдельное explicit overwrite-действие, default `false`;
- `import_max_upload_bytes` — browser upload limit;
- `bundle_max_archive_bytes` — physical incoming/verifier archive limit;
- `bundle_max_extracted_bytes`;
- `bundle_max_member_count`;
- `operation_disk_reserve_bytes`;
- `operation_max_concurrent`.

Backend валидирует bounds и взаимосвязи параметров и пишет audit только с безопасными field names/classification. Большинство значений начинают действовать для последующих операций сразу; `operation_max_concurrent` имеет явную restart-required semantics, потому что текущий in-process semaphore создаётся при startup.

Включение `import_allow_overwrite` **не делает overwrite автоматическим**: TARGET operator/admin всё равно должен отдельно подтвердить конфликтный execute, а `UNKNOWN/ERROR` остаются блокирующими.

Подробности: [transfer-policies.md](transfer-policies.md).

## 13. Persistent data layout

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

## 14. Backup: что обязательно сохранять

Полный backup установки — не только SQLite.

Минимальный защищаемый набор:

- `/app/data/harbor-transfer-portal.db`;
- `/app/data/secrets/`;
- SOURCE: `/app/data/keys/source-signing-private.pem`;
- TARGET: `/app/data/keys/trusted-source/`;
- необходимые receipts/history metadata;
- configuration, достаточная для восстановления роли установки;
- retained incoming/outgoing packages — только если этого требует организационная retention policy.

Backup SOURCE с private signing key является чувствительным secret backup. Не включайте его в release archive и не храните рядом с публичным SOURCE public key как обычный несекретный artifact.

## 15. Consistent backup текущего Compose

Ниже — baseline для текущего Compose, а не финальный #28 disaster-recovery contract.

### 15.1. Остановить запись

Для согласованного snapshot остановите сервисы:

```bash
docker compose stop frontend backend
```

### 15.2. Снять archive `/app/data`

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

### 15.3. Вернуть сервисы

```bash
docker compose up -d
```

После backup проверьте health/readiness.

## 16. Restore текущего Compose

Restore выполняйте только в ожидаемую установку с правильным `PORTAL_CONTOUR` и после проверки источника backup.

### 16.1. Остановить сервисы

```bash
docker compose stop frontend backend
```

### 16.2. Очистить восстанавливаемый volume

Это destructive step. Выполняйте его только после подтверждения, что выбран правильный deployment:

```bash
docker compose run --rm -T --no-deps \
  --user root \
  --entrypoint sh \
  backend -c 'find /app/data -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +'
```

### 16.3. Восстановить archive

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

### 16.4. Запустить и проверить

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

## 17. Upgrade текущего development/runtime Compose

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

## 18. Disk capacity и package limits

Bundle и operation limits имеют два источника:

- bootstrap/default значения в `.env`/`Settings`;
- поддерживаемые runtime overrides из admin Settings, описанные выше.

Основные Bundle settings:

- `BUNDLE_MAX_ARCHIVE_BYTES`;
- `BUNDLE_MAX_EXTRACTED_BYTES`;
- `BUNDLE_MAX_MEMBER_COUNT`;
- `BUNDLE_MAX_PATH_BYTES`;
- `BUNDLE_MAX_METADATA_BYTES`;
- `BUNDLE_MAX_COMPRESSION_RATIO`;
- `BUNDLE_MAX_TRUSTED_KEYS`;
- `BUNDLE_KEY_MATERIAL_MAX_BYTES`.

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

## 19. Restart, shutdown и фоновые операции

Resume середины Skopeo/Helm-команды после restart в v1 не поддерживается.

При неожиданном restart уже захваченная non-terminal operation, кроме `READY`, переводится в безопасный `FAILED` (`operation_interrupted_restart`). При штатном shutdown аналогичная операция завершается `operation_interrupted_shutdown`.

`READY` является единственным waiting state, для которого stale worker ownership освобождается с сохранением подготовленного workspace.

Поэтому перед planned maintenance:

- по возможности не начинайте новые длительные операции;
- дождитесь terminal state текущих операций;
- не считайте restart способом «продолжить с середины» transfer command.

Подробности: [operation-manager.md](operation-manager.md).

## 20. Логи и audit

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

Persisted audit фиксирует security-sensitive administrative changes, включая:

- Harbor settings/credential/CA;
- локальных пользователей;
- transfer policies;
- SOURCE signing key install/rotation;
- TARGET trusted-key add/replace/enable/disable/remove.

Audit metadata содержит actor/action и безопасные identifiers/field names; password, Harbor credential, private key PEM и другое secret material туда не должны попадать.

Каталог `/app/data/logs` зарезервирован в persistent layout, но наличие каталога не означает, что current backend автоматически пишет туда весь stdout/stderr. Источником истины для current Compose logs остаётся container logging, пока отдельная file-log policy не реализована и не документирована.

## 21. Retention

Автоматическая product retention/cleanup для incoming/outgoing bundles, receipts и history **не реализована** и не должна подразумеваться настройками UI.

До появления отдельного tested lifecycle:

- определите организационную policy хранения/backup;
- не удаляйте `data/secrets` и key material как «временные файлы»;
- не очищайте `READY` workspace вручную без понимания operation state;
- не смешивайте cleanup transfer payload с backup cleanup;
- не храните bundle бесконечно только потому, что каталог persistent.

Если автоматическая retention понадобится, она должна получить отдельный lifecycle/code/tests/audit contract, а не появляться скрытым side effect существующей настройки.

## 22. Проверка после установки или изменения конфигурации

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
10. SOURCE имеет private signing key **или** TARGET имеет правильный enabled trust set;
11. key fingerprint сверён по доверенному организационному каналу;
12. transfer policies/limits соответствуют capacity и security policy;
13. persistent volume не является ephemeral bind/tmp storage;
14. backup procedure проверена в контролируемой среде;
15. `make docs-check`/CI не показывает рассинхрон документации.

Для repository-level Compose smoke:

```bash
./deploy/smoke-compose.sh
```

Smoke test предназначен прежде всего для development/CI и не заменяет площадочный operational acceptance.

## 23. Что пока не следует считать готовым

Текущий application v1 уже содержит SOURCE export wizard/orchestration, TARGET intake/preview/import wizard/orchestration, History/CSV/PDF reports, admin user management, runtime transfer policies и managed signing/trust-key lifecycle.

Отдельно **ещё не заявляются как завершённые release capabilities**:

- production-tested automatic retention/cleanup;
- финальный offline installation/upgrade/uninstall kit;
- clean-VM one-command installation acceptance;
- release-grade restore/rollback qualification;
- полный isolated SOURCE → physical transfer → TARGET acceptance E2E из #28.

Эти ограничения не следует описывать как отсутствие текущих browser transfer flows: они относятся к release/operations qualification следующего этапа.

## 24. Связанные документы

- [Карта документации](README.md)
- [User Guide](user-guide.md)
- [Troubleshooting](troubleshooting.md)
- [Deployment/runtime Compose](../deploy/README.md)
- [Архитектура](architecture.md)
- [Security/trust model](security.md)
- [OperationManager](operation-manager.md)
- [Transfer policies](transfer-policies.md)
- [Key management](key-management.md)
- [Package service и key model](package-service.md)
- [Offline Bundle Protocol v1](offline-bundle-v1.md)
- [Testing/CI](testing.md)

Если этот документ расходится с current code, `.env.example`, accepted ADR или `deploy/README.md`, расхождение является documentation defect и должно исправляться вместе с соответствующим изменением.