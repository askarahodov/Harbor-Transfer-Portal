# Руководство администратора Harbor Transfer Portal

**Статус:** актуальная эксплуатационная инструкция baseline **v1.0.0** для `SOURCE` и `TARGET`, включая shipped offline installation kit.

Этот документ описывает администрирование одной установки Harbor Transfer Portal: offline install, local Harbor, users/RBAC, credentials/CA, signing/trust keys, transfer policies, persistent data и lifecycle. Пошаговый operator flow SOURCE → physical transfer → TARGET описан в [user-guide.md](user-guide.md). Нормативный переносимый формат задаёт [Offline Bundle Protocol v1](offline-bundle-v1.md), security model — [security.md](security.md).

## 1. Две независимые установки

Harbor Transfer Portal разворачивается отдельно:

- `SOURCE` — только в исходном контуре и только со своим local Harbor;
- `TARGET` — только в целевом контуре и только со своим local Harbor.

Одна installation не хранит credentials противоположного Harbor и не создаёт сетевой путь между контурами. Role фиксируется:

```text
PORTAL_CONTOUR=SOURCE
```

или:

```text
PORTAL_CONTOUR=TARGET
```

Не переключайте рабочую installation SOURCE ↔ TARGET как обычную операционную процедуру: contours должны иметь отдельные state, secrets, keys и persistent data.

## 2. Production/offline installation v1.0.0

Versioned release archive строится в контролируемой release environment из заранее собранных images:

```bash
./deploy/build-release-images.sh 1.0.0
./deploy/build-offline-kit.sh 1.0.0
```

В закрытый контур передаются:

```text
harbor-transfer-portal-v1.0.0-offline-install.tar.gz
harbor-transfer-portal-v1.0.0-offline-install.tar.gz.sha256
```

После проверки внешнего checksum и распаковки:

```bash
PORTAL_CONTOUR=SOURCE ./install.sh
```

или:

```bash
PORTAL_CONTOUR=TARGET ./install.sh
```

Installer проверяет internal checksums, Docker Engine/Compose, architecture и disk capacity, выполняет `docker load`, проверяет exact local image identity и запускает Compose только с bundled images (`--no-build --pull never`). `.env` создаётся локально, JWT secret генерируется на площадке; существующий regular `.env` не перезаписывается молча.

Release kit **не содержит** Harbor credentials, JWT secret, SOURCE private key, TARGET trust material, SQLite DB, receipts/history или backup archives.

Полная installation/lifecycle процедура: [deploy/offline/README.md](../deploy/offline/README.md).

## 3. Предварительные требования площадки

Нужны:

- Docker Engine;
- Docker Compose v2;
- поддерживаемая architecture release image (`amd64`/`arm64` согласно конкретному kit);
- достаточный disk capacity;
- local Harbor текущего contour;
- отдельный Harbor service account;
- для SOURCE — Ed25519 signing private key;
- для TARGET — trusted SOURCE public key(s);
- утверждённый physical transfer process/media.

Runtime закрытого контура не должен зависеть от internet/CDN. Не выполняйте `docker compose build`, `npm install`, `pip install` или image pull как часть штатной offline установки.

## 4. Первый запуск и bootstrap admin

После `install.sh` Portal доступен через frontend HTTP endpoint, по умолчанию на `PORTAL_HTTP_PORT=8080`.

Проверки:

```text
GET /api/health
GET /api/ready
GET /healthz
```

Backend entrypoint применяет Alembic migrations до запуска API.

Создайте первый local admin, передавая password только как временную environment variable процесса:

```bash
export BOOTSTRAP_ADMIN_PASSWORD='replace-with-a-strong-password'
docker compose --env-file .env -f compose.yaml exec -T \
  -e BOOTSTRAP_ADMIN_PASSWORD="$BOOTSTRAP_ADMIN_PASSWORD" \
  backend python -m app.auth.cli --username admin
unset BOOTSTRAP_ADMIN_PASSWORD
```

После этого войдите через web UI.

## 5. Local users и RBAC

Роли:

| Роль | Возможности |
|---|---|
| `admin` | settings, users, policies, signing/trust keys, административные операции |
| `operator` | SOURCE export, TARGET intake/import, разрешённые operation actions |
| `viewer` | read-only dashboard/history/reports |

Admin UI **«Пользователи»** позволяет создавать local users, менять role/status/password. Security-sensitive mutation требует подтверждения. Password/hash не возвращаются API/UI.

Backend защищает от удаления последнего active admin через role/status change. Hard-delete пользователей не используется, чтобы сохранять audit identity history.

Подробности: [admin-user-management.md](admin-user-management.md).

## 6. Настройка local Harbor

Используйте отдельный service account минимально необходимых прав:

- SOURCE — read/pull metadata/artifacts разрешённых projects;
- TARGET — read + push/write разрешённых target projects/repositories.

Не выдавайте global Harbor admin только ради удобства.

Через admin **«Настройки локального Harbor»** задаются:

- URL local Harbor;
- username/service account;
- managed credential;
- TLS verification;
- custom CA;
- connection test.

`HARBOR_URL` должен указывать на Harbor origin без embedded credentials, query/fragment или произвольного subpath.

## 7. Harbor credential

Предпочтительный persistent path:

```text
HARBOR_MANAGED_SECRET_FILE=./data/secrets/harbor-password
```

Credential, установленный через UI, сохраняется server-side с restrictive permissions и не возвращается обратно.

Fallback order:

1. managed credential file;
2. `HARBOR_PASSWORD_FILE`;
3. `HARBOR_PASSWORD` environment.

`HARBOR_PASSWORD` оставлен для bootstrap compatibility и не является рекомендуемым постоянным storage.

Rotation:

1. создайте новый credential в local Harbor;
2. сохраните его в Portal Settings;
3. выполните connection test;
4. после успеха отзовите старый credential.

Audit фиксирует action/field metadata без secret value.

## 8. TLS и private CA

Штатно:

```text
HARBOR_VERIFY_TLS=true
```

При private PKI:

1. оставьте verification включённой;
2. загрузите PEM/CRT CA через admin Settings;
3. выполните connection test;
4. убедитесь, что соединение успешно без insecure fallback.

Managed CA:

```text
HARBOR_MANAGED_CA_FILE=./data/secrets/harbor-ca.pem
```

`HARBOR_VERIFY_TLS=false` не является штатным исправлением x509/private-CA ошибки.

## 9. SOURCE signing key

SOURCE создаёт Bundle v1 и хранит Ed25519 private key только локально.

Создать key pair можно на доверенной административной машине:

```bash
umask 077
openssl genpkey -algorithm ED25519 -out source-signing-private.pem
openssl pkey -in source-signing-private.pem -pubout -out source-signing-public.pem
```

Нормальная установка/rotation выполняется через **Settings → Signing и trust keys**. UI показывает status/fingerprint, но не возвращает private key после сохранения. Backend валидирует Ed25519, нормализует key и хранит его с restrictive permissions.

Private key нельзя передавать:

- в Offline Bundle;
- на TARGET;
- в release kit;
- в Git/logs/issues/chats;
- как постоянный command-line argument.

Rotation выполняйте с overlap: сначала добавьте новый public key на TARGET, затем переключайте SOURCE private key.

## 10. TARGET trusted SOURCE keys

TARGET хранит только public keys:

```text
BUNDLE_TRUSTED_PUBLIC_KEYS_DIR=./data/keys/trusted-source
```

Через UI можно добавить/заменить, enable/disable и удалить Ed25519 public key по fingerprint. Private/malformed/oversized material отклоняется.

Public key должен поступать по доверенному организационному каналу. Для rotation используйте overlap двух enabled keys, затем disable/remove старый после migration window.

Подробнее: [key-management.md](key-management.md).

## 11. Transfer policies и limits

Admin Settings поддерживает runtime policies, включая:

- `import_allow_overwrite` — default `false`;
- upload/archive/extracted size limits;
- member/path/metadata limits;
- disk reserve;
- operation concurrency.

Включение `import_allow_overwrite` не делает overwrite автоматическим: operator/admin всё равно подтверждает конкретные `CONFLICT` items. `UNKNOWN/ERROR` остаются блокирующими.

Не увеличивайте limits только чтобы принять неожиданно большой или malformed bundle. Оцените реальный payload, temporary workspace, extracted copy, concurrency и disk reserve.

Подробнее: [transfer-policies.md](transfer-policies.md).

## 12. Persistent data

Release Compose использует stable named volume `harbor-transfer-portal_portal-data`, монтируемый в `/app/data`.

Основные данные:

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

Backend работает под UID/GID `10001`. Не используйте `docker compose down -v` как обычный restart: `-v` удаляет persistent volume.

## 13. Backup

Для offline installation используйте shipped:

```bash
./backup.sh
```

Backup включает `.env` и persistent `/app/data`, следовательно содержит:

- SQLite/history/audit state;
- Harbor managed secret/CA;
- SOURCE private key либо TARGET trust set;
- receipts и другую retained metadata.

Такой archive является sensitive secret backup. Храните его отдельно от release kit и публичного trust material.

Script создаёт internal/external checksums и использует локальный backend image без network pull.

## 14. Restore

Restore выполняется matching-version kit и требует explicit confirmation:

```bash
./restore.sh /secure/backups/harbor-transfer-portal-backup-v1.0.0-....tar.gz --confirm-restore
```

До destructive mutation script проверяет external/internal checksums, allowlist backup members, product/version/contour/volume identity и unsafe archive paths/types.

Restore не является автоматическим Alembic downgrade. Для recovery сначала восстановите matching version, подтвердите health, затем выполняйте штатный upgrade.

## 15. Upgrade

Распакуйте новый kit отдельно и выполните из него:

```bash
./upgrade.sh /path/to/previous-install
```

Upgrade сначала создаёт обязательный pre-upgrade backup, затем загружает bundled images, переносит прежний `.env`, меняя только `PORTAL_VERSION`, и запускает `--no-build --pull never --wait`.

При startup failure configuration возвращается к предыдущей версии, но уже применённая DB migration может требовать restore pre-upgrade backup. Автоматический database rollback не обещается.

## 16. Uninstall

Безопасный uninstall:

```bash
./uninstall.sh
```

останавливает workload, но сохраняет `.env` и persistent volume.

Destructive data purge требует явного подтверждения:

```bash
PORTAL_CONFIRM_PURGE=DELETE_PORTAL_DATA ./uninstall.sh --purge-data
```

Даже purge не удаляет `.env` и release files автоматически.

## 17. SOURCE → TARGET штатный workflow

После admin configuration operator работает через browser:

1. SOURCE `/export`: выбирает точные image/chart versions из local Harbor;
2. проверяет preview/digests;
3. запускает export и ждёт `COMPLETED`;
4. скачивает `.htp.tar.gz` и `.sha256`;
5. физически переносит файлы по утверждённой процедуре;
6. TARGET `/import`: upload archive или discovery готовой archive+sidecar пары;
7. TARGET проверяет archive/schema/signature/checksums до mutation;
8. operator анализирует `NEW/SAME/CONFLICT/UNKNOWN/ERROR`;
9. запускает допустимый import;
10. проверяет receipt/history/CSV/PDF reports.

`SAME` — idempotent skip. `CONFLICT` блокируется по умолчанию. `UNKNOWN/ERROR` не трактуются как `NEW`.

Подробности: [user-guide.md](user-guide.md).

## 18. Restart и interrupted operations

Resume середины Skopeo/Helm subprocess после restart baseline v1 не поддерживает. Active interrupted operations reconciles в safe terminal state; SOURCE cleanup не должен оставлять ready-looking partial publication. TARGET `READY` может сохраняться, пока mutation не началась.

Не определяйте operation state по text logs — источник истины persisted DB state/API.

## 19. Release identity v1.0.0

Оператор видит version в UI; `/api/health` возвращает ту же runtime version. SOURCE Bundle записывает `source.portal_version`.

Release tooling fail-closed проверяет согласованность archive metadata и OCI labels. Stale image от другого commit/revision не должен упаковываться как текущий release.

Release notes: [release-notes-v1.0.0.md](release-notes-v1.0.0.md). Changelog: [CHANGELOG.md](../CHANGELOG.md).

## 20. Release qualification

Release-critical behavior автоматически проверяется CI:

- backend/frontend checks;
- protocol/security gates по scope;
- real Skopeo/Helm local-registry integration;
- Compose smoke;
- clean-host offline install одного archive в SOURCE и TARGET;
- isolated SOURCE → physical bundle → TARGET flow с separate registries;
- digest/Helm verification, receipt/history/report;
- replay skip, conflict default-deny, tamper rejection;
- documentation links и общий `quality-gate`.

Эти проверки являются частью текущего v1 release process, а не будущей работой.

## 21. Diagnostics

Начинайте с:

```bash
docker compose --env-file .env -f compose.yaml ps
docker compose --env-file .env -f compose.yaml logs backend
docker compose --env-file .env -f compose.yaml logs frontend
```

Проверяйте health/readiness, local Harbor connection test, key status и operation error code. Не публикуйте raw secret-bearing logs.

Пошаговые сценарии: [troubleshooting.md](troubleshooting.md).

## 22. Что остаётся внешним операционным контролем

Portal не заменяет:

- host/OS hardening;
- production TLS termination/network policy;
- Harbor account governance;
- physical media custody/malware controls;
- backup retention/escrow;
- организационный release/change approval.

Эти controls должны быть определены площадкой поверх application fail-closed boundaries.
