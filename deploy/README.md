# Развертывание Harbor Transfer Portal через Docker Compose

**Статус:** актуальная deployment-документация baseline **v1.0.0**.

В проекте существуют два разных workflow, которые нельзя смешивать:

1. **development/runtime Compose** из корня repository — может собирать images и использовать внешние build dependencies;
2. **offline release kit** — versioned archive с заранее собранными images, предназначенный для установки в закрытом контуре без internet/pull/build.

Production/offline процедура: [deploy/offline/README.md](offline/README.md). Этот документ в основном описывает topology, configuration и development Compose.

## 1. Runtime-модель

Одна установка содержит два application services:

- `backend` — FastAPI, SQLite, Skopeo, Helm, migrations и transfer services; доступен только во внутренней Compose network;
- `frontend` — Nginx со собранным Vue SPA и reverse proxy `/api/` на backend.

Хостовый HTTP-порт публикует только frontend. Браузер обращается к API same-origin через Nginx.

Каждая installation знает только свой настроенный local Harbor и не хранит credentials противоположного Harbor. Один и тот же deployment в каждый момент работает в runtime role `SOURCE` или `TARGET`; переключение роли не создаёт сетевой путь между физически изолированными контурами и не переключает Harbor configuration.

В production air-gap процессе физически раздельные контуры обычно имеют собственные installations Portal. Universal runtime mode нужен для единого software/deployment contract и поддерживаемого переключения роли конкретного instance, а не для объединения сетево изолированных Harbor.

`PORTAL_CONTOUR` задаёт только bootstrap default **новой базы**, например:

```text
PORTAL_CONTOUR=SOURCE
```

или:

```text
PORTAL_CONTOUR=TARGET
```

После первого startup authoritative runtime mode сохраняется в SQLite. Последующее изменение `PORTAL_CONTOUR` или restart не должны переопределять сохранённую роль; `operator`/`admin` переключает `SOURCE ↔ TARGET` через UI/runtime API при отсутствии блокирующих operations. Полный contract: [runtime-mode.md](../docs/runtime-mode.md).

## 2. Development Compose и offline release

### Development/runtime Compose

Source Compose поддерживается на Linux с Docker Engine и на Windows с Docker Desktop в режиме Linux containers. Canonical cross-platform launcher: [docs/development.md](../docs/development.md). На Windows для этого workflow не требуются WSL, Git Bash или GNU Make.

Linux — запуск исходного checkout:

```bash
./run.sh
```

Windows CMD:

```bat
run.cmd
```

Оба launcher создают `.env` только при его отсутствии и запускают `docker compose up -d --build --force-recreate` без host Python. Дополнительный developer CLI (`python3 tools/dev.py setup`, `doctor`, `up`; Windows: `py -3`) и PowerShell wrapper `dev.ps1` необязательны.

Корневой `compose.yaml` содержит `build:` sections. Backend image build может получать pinned Helm archive и OS/Python dependencies, frontend build — npm dependencies. Поэтому `docker compose up -d --build` допустим только там, где build environment имеет необходимые разрешённые источники.

Runtime использует стандартную Compose bridge network: backend не публикует `:8000` на host и доступен frontend только как `backend:8000`; frontend один публикует container `:8080` через `PORTAL_HTTP_BIND:PORTAL_HTTP_PORT`. Поэтому source workflow не требует Docker host-network feature ни на Linux, ни на Docker Desktop.

### Offline release

Release build выполняется заранее в контролируемой среде:

```bash
./deploy/build-release-images.sh 1.0.0
./deploy/build-offline-kit.sh 1.0.0
```

Kit содержит versioned backend/frontend image tar, image-only Compose, installer/lifecycle scripts, configuration template, checksums, release manifest, changelog и release notes.

В закрытом контуре `install.sh` использует `docker load` и запускает Compose с:

```text
--no-build --pull never
```

Release Compose дополнительно задаёт `pull_policy: never`. Runtime не должен обращаться к PyPI/npm/apt/get.helm.sh/CDN.

Полная инструкция: [offline/README.md](offline/README.md).

## 3. Persistent volume

Named volume `portal-data` монтируется backend в `/app/data`.

Типовая структура:

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

Backend image работает под UID/GID `10001` (`htp`). Runtime files, которые приложение должно менять, должны оставаться доступны этому пользователю.

Обычный:

```bash
docker compose down
```

не удаляет named volume. Команда `docker compose down -v` удаляет persistent data и не является штатным restart/update step.

Offline release использует стабильное Compose project name `harbor-transfer-portal`, поэтому volume identity не зависит от versioned каталога kit.

## 4. Development `.env`

При первом source-запуске `run.sh` или `run.cmd` создаёт `.env` из `.env.example` только если файл отсутствует. При повторном запуске существующий `.env` **не перезаписывается**. Ручная генерация `JWT_SECRET` не нужна: если он не задан, backend создаёт случайный секрет в persistent volume и переиспользует при restart. Опциональный `tools/dev.py setup` создаёт локальный `.env` с секретом.

Для реальной передачи после bootstrap настройте действующий local Harbor profile в web UI (Settings) и проверьте соединение. Значения `HARBOR_URL` / `HARBOR_USER` в `.env.example` — placeholders, а не готовое подключение. `PORTAL_CONTOUR` задаёт только первоначальную роль пустой DB; после инициализации текущий mode берётся из persisted runtime state. По умолчанию frontend слушает на loopback `127.0.0.1`; для удалённого authenticated browser traffic нужен согласованный HTTPS-терминатор.

`.env` исключён из Git и build context. Не коммитьте реальные passwords, tokens, `JWT_SECRET`, private signing keys, backup archives или private certificates.

В offline kit `.env` не поставляется: installer создаёт его локально и генерирует случайный JWT secret, не перезаписывая существующий regular file молча.

## 5. Local Harbor configuration

После bootstrap admin предпочтительный путь — настроить local Harbor через web UI **«Настройки локального Harbor»**.

Поддерживаются:

- URL local Harbor;
- service-account username;
- managed credential installation/rotation;
- TLS verification;
- custom CA upload/removal;
- connection test.

Предпочтительный persistent credential path:

```text
HARBOR_MANAGED_SECRET_FILE=./data/secrets/harbor-password
```

Fallback order backend:

1. managed credential file;
2. `HARBOR_PASSWORD_FILE`;
3. `HARBOR_PASSWORD` environment.

`HARBOR_PASSWORD` — bootstrap compatibility, а не рекомендуемое постоянное production storage.

## 6. TLS и private CA

Штатно:

```text
HARBOR_VERIFY_TLS=true
```

Для private PKI загрузите PEM/CRT CA bundle через admin Settings и выполните connection test. Managed CA хранится в persistent data:

```text
HARBOR_MANAGED_CA_FILE=./data/secrets/harbor-ca.pem
```

`HARBOR_VERIFY_TLS=false` не является штатным способом исправить x509/private-CA проблему. Silent fallback на insecure TLS отсутствует.

## 7. Signing и trust keys

SOURCE хранит Ed25519 private signing key только локально. TARGET хранит только trusted SOURCE public keys.

Нормальный lifecycle выполняется через **Settings → Signing и trust keys**. Key material валидируется backend; normal API/UI не возвращает SOURCE private key после сохранения.

Private signing key запрещено включать в delivery bundle или offline installation kit.

Подробности: [key-management.md](../docs/key-management.md).

## 8. Development start

Используйте штатный `./run.sh` (Linux) или `run.cmd` (Windows CMD): они подготавливают `.env` и запускают Compose build. Для ручного управления **после создания `.env`**:

```bash
docker compose config
docker compose up -d --build --force-recreate
```

Make/host Python не требуются для source quick start; расширенные developer-команды см. в [docs/development.md](../docs/development.md).

Portal доступен на:

```text
http://localhost:${PORTAL_HTTP_PORT:-8080}
```

Health endpoints:

```text
/api/health
/api/ready
/healthz
```

Backend entrypoint применяет Alembic migrations до запуска API. Migration failure не маскируется как healthy startup.

## 9. Bootstrap administrator

На **новой установке с пустой таблицей пользователей** backend автоматически создаёт временного `admin/admin`; отдельная CLI-команда создания пользователя не требуется. При первом входе Portal принудительно открывает смену пароля: новый пароль не менее 12 символов, а защищённые API недоступны до смены (`403 password_change_required`). После успешной смены старый пароль и выданные ранее JWT перестают действовать. При повторном запуске действующий admin не создаётся повторно и пароль не сбрасывается.

Стартовый пароль предназначен **только для loopback/bootstrap**: не публикуйте HTTP вход с `admin/admin` в сеть. Для удалённого браузера используйте согласованный HTTPS-терминатор. В Settings после входа настройте local Harbor profiles, пользователей, политики и trust keys. См. [admin guide](../docs/admin-guide.md) и [security model](../docs/security.md).

## 10. SOURCE/TARGET operator flow

После configuration штатная передача не требует CLI Skopeo/Helm:

```text
SOURCE browser
  → explicit SOURCE Harbor profile selection
  → local Harbor artifact selection
  → bundle + .sha256 + signed .htp-handoff.json download
  → approved physical transfer
TARGET browser
  → explicit TARGET Harbor profile selection
  → upload/discovery полного delivery triplet
  → signed handoff verification
  → Bundle v1 signature/checksum/schema preview
  → NEW/SAME/CONFLICT decision
  → import
  → receipt/history/reports
```

Подробности: [user-guide.md](../docs/user-guide.md).

## 11. Backup / restore / upgrade / uninstall

Для production/offline install используйте scripts, поставляемые kit:

```text
backup.sh
restore.sh
upgrade.sh
uninstall.sh
```

Backup включает `.env` и `/app/data`, поэтому содержит secrets и требует защищённого хранения. Restore требует matching-version kit и explicit destructive confirmation. Upgrade всегда создаёт pre-upgrade backup; автоматический Alembic downgrade/rollback не обещается.

Подробности: [offline/README.md](offline/README.md) и [offline-lifecycle.md](../docs/offline-lifecycle.md).

## 12. Logging и diagnostics

Compose использует `json-file` logging с bounded rotation. Application logs должны сохранять request/operation correlation и не раскрывать credentials, JWT, key material или raw secret-bearing command data.

При проблемах проверяйте:

```bash
docker compose ps
docker compose logs backend
docker compose logs frontend
```

а затем используйте [troubleshooting.md](../docs/troubleshooting.md).

## 13. Release identity

Canonical product version baseline v1 — `1.0.0`. Release tooling проверяет согласованность:

- backend package/runtime version;
- backend/frontend image tags;
- OCI `org.opencontainers.image.version`;
- OCI `org.opencontainers.image.revision`;
- `release-version.txt`;
- `release-manifest.json`;
- runtime `/api/health.version`;
- UI runtime version;
- SOURCE Bundle `source.portal_version`.

Packaging stale image от другого source revision должен fail closed.

## 14. CI / qualification

Development/runtime smoke:

```bash
make smoke-compose
```

Release-sensitive paths дополнительно включают:

- Skopeo/Helm disposable-registry integration;
- clean-host offline installation qualification;
- isolated SOURCE → physical bundle → TARGET acceptance;
- final `quality-gate`.

Clean-host qualification реально удаляет release-tagged images до install phase и проверяет один и тот же archive в SOURCE/TARGET. Isolated acceptance использует независимые local registries и не оставляет TARGET прямой зависимости от SOURCE fixture.

Test-selection policy: [testing.md](../docs/testing.md).

## 15. Что не является production contract

Корневой development `compose.yaml` и `docker compose up -d --build` удобны для разработки, но не заменяют offline release artifact. Закрытая production площадка должна получать заранее построенный versioned kit и выполнять local install/lifecycle по `deploy/offline/README.md`.
