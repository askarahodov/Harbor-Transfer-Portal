# Harbor Transfer Portal

![Harbor Transfer Portal](docs/img/devops-logo-white.png)

[![CI](https://github.com/askarahodov/Harbor-Transfer-Portal/actions/workflows/ci.yml/badge.svg)](https://github.com/askarahodov/Harbor-Transfer-Portal/actions/workflows/ci.yml)

**Harbor Transfer Portal** — локальный веб-портал для безопасной офлайн-передачи container images и Helm OCI charts между физически и сетево изолированными Harbor-контурами.

Портал нужен там, где SOURCE и TARGET не имеют прямого сетевого соединения: оператор подготавливает подписанный пакет на SOURCE, переносит его разрешённым физическим способом и проверяет перед импортом на TARGET.

> **Статус:** функциональный объём **v1.0.0** реализован и прошёл release qualification, включая clean-host offline install и isolated SOURCE → physical bundle → TARGET acceptance. Production rollout по-прежнему требует локального change/release approval и проверки инфраструктуры конкретного контура.

## Начните здесь

| Если вы… | Основной документ |
|---|---|
| выполняете перенос как оператор | [Пользовательское руководство](docs/user-guide.md) |
| хотите только просматривать историю и результаты | [Пользовательское руководство](docs/user-guide.md) |
| администрируете Portal, Harbor, пользователей, CA или ключи | [Руководство администратора](docs/admin-guide.md) |
| устанавливаете Portal в закрытом контуре | [Offline installation kit](deploy/offline/README.md) |
| устраняете ошибку или отказ | [Troubleshooting](docs/troubleshooting.md) |
| хотите понять продукт без технических деталей | [Паспорт проекта](docs/project-passport.md) |
| разрабатываете или сопровождаете код | [Карта документации](docs/README.md) и [CONTRIBUTING.md](CONTRIBUTING.md) |

Для штатного пользовательского переноса не нужно вручную работать с `skopeo`, `helm`, `tar`, `sha256sum` или Harbor CLI.

## Как работает перенос

```text
Harbor SOURCE
    ↓
Portal в SOURCE role
    ↓
подписанный Offline Bundle v1
(.htp.tar.gz + .sha256)
    ↓
разрешённый физический носитель
    ↓
Portal в TARGET role
    ↓
проверка checksum / schema / signature / conflict policy
    ↓
Harbor TARGET
```

Между SOURCE и TARGET **нет прямого сетевого соединения**. Каждая установка работает только со своим локальным Harbor и не хранит credentials противоположного контура.

Один и тот же software/deployment поддерживает runtime roles `SOURCE` и `TARGET`. В production air-gap сценарии физически раздельные контуры обычно имеют собственные установки Portal.

## Операторский сценарий

### 1. SOURCE — подготовить поставку

1. Войти в Portal и убедиться, что текущая runtime role — `SOURCE`.
2. Выбрать точные container images и/или Helm OCI charts из локального Harbor.
3. Проверить preview: repository, tag/version, digest и состав поставки.
4. Запустить export и дождаться terminal state `COMPLETED`.
5. Скачать **оба** файла: `*.htp.tar.gz` и соответствующий `.sha256`.

### 2. Физически перенести пакет

Перенесите archive и `.sha256` вместе на разрешённом носителе по принятой в организации процедуре. Не распаковывайте и не редактируйте bundle вручную.

### 3. TARGET — проверить и импортировать

1. Войти в Portal и убедиться, что текущая runtime role — `TARGET`.
2. Передать Portal полученный archive через browser upload либо configured incoming directory/transfer media workflow.
3. Дождаться backend verification checksum, Bundle v1 schema/canonical manifest и Ed25519 signature.
4. Просмотреть preview состояний `NEW`, `SAME`, `CONFLICT`, `UNKNOWN`, `ERROR`.
5. Запустить разрешённый import.
6. Проверить результат, receipt, History и при необходимости скачать отчёт.

Полная пошаговая инструкция с ролями, состояниями и пользовательскими ошибками: [docs/user-guide.md](docs/user-guide.md).

## Что важно для безопасности

- SOURCE и TARGET не соединяются portal-to-portal или Harbor-to-Harbor.
- Runtime закрытого контура не зависит от internet/CDN.
- Bundle v1 содержит canonical `manifest.json`, Ed25519 `manifest.sig` и SHA-256 integrity metadata.
- SHA-256 проверяет целостность, но **не заменяет** цифровую подпись.
- TARGET считает полученный bundle недоверенным до успешного verifier flow.
- `CONFLICT` не приводит к неявной перезаписи target artifact.
- `UNKNOWN` и `ERROR` блокируют mutation, а не трактуются как `NEW`.
- Harbor credentials, JWT secrets и signing private keys не хранятся в Git и не включаются в bundle.
- TLS verification включена по умолчанию; private CA настраивается явно.
- SOURCE download считается готовым только после verified publication и terminal `COMPLETED`.
- TARGET UI показывает trust status только как projection backend verification; браузер не является источником криптографического решения.

Полная модель угроз и доверия: [docs/security.md](docs/security.md). Нормативный формат пакета: [docs/offline-bundle-v1.md](docs/offline-bundle-v1.md).

## Что входит в v1.0.0

| Возможность | Статус |
|---|---|
| Local users, JWT, RBAC | реализовано |
| SOURCE/TARGET runtime roles | реализовано |
| Harbor settings, managed credential, custom CA | реализовано |
| Harbor browser и выбор exact image/chart versions | реализовано |
| Skopeo container transfer | реализовано и проверяется integration gate |
| Helm OCI transfer | реализовано и проверяется integration gate |
| Offline Bundle Protocol v1, signing и verification | реализовано |
| Persistent operation state, progress, cancel, restart reconciliation | реализовано |
| SOURCE export wizard и disk-backed download | реализовано |
| TARGET intake, preview, conflict handling и import wizard | реализовано |
| History, audit, CSV/PDF reports, immutable import receipt | реализовано |
| Offline installation kit, backup/restore/upgrade/uninstall | реализовано |
| Clean-host SOURCE/TARGET qualification | обязательный CI gate |
| Isolated SOURCE → physical bundle → TARGET acceptance | обязательный CI gate |
| Release identity v1.0.0 в images/UI/API/bundle metadata | реализовано |

Текущий архитектурный статус и component boundaries: [docs/architecture.md](docs/architecture.md).

## Установка в закрытом контуре

Для production/offline installation используйте **versioned offline kit**, а не development Compose build. Release archive содержит заранее собранные images и запускается без online build/pull.

Основная инструкция: [deploy/offline/README.md](deploy/offline/README.md).

Администратору также нужны:

- [docs/admin-guide.md](docs/admin-guide.md) — bootstrap, Harbor, users, policies, keys, backup/restore и эксплуатация;
- [docs/runtime-mode.md](docs/runtime-mode.md) — persistent SOURCE/TARGET role и безопасное переключение;
- [docs/key-management.md](docs/key-management.md) — SOURCE signing identity и TARGET trusted keys;
- [docs/troubleshooting.md](docs/troubleshooting.md) — диагностика и безопасные способы восстановления.

Release images и kit формируются в разрешённой build/release среде:

```bash
./deploy/build-release-images.sh 1.0.0
./deploy/build-offline-kit.sh 1.0.0
```

В закрытом контуре используются уже собранные release artifacts согласно offline guide.

## Для разработчиков

### Архитектура и стек

| Уровень | Технологии / роль |
|---|---|
| Frontend | Vue 3, TypeScript, Vite, Pinia, Vue Router, Axios, Element Plus |
| Web/runtime | Nginx + same-origin `/api/` proxy |
| Backend | Python 3.12, FastAPI, Pydantic |
| Persistence | SQLite, SQLAlchemy, Alembic |
| Container transfer | Skopeo |
| Helm transfer | Helm OCI |
| Background execution | persistent in-process `asyncio` OperationManager для baseline v1 |
| Packaging/security | `.htp.tar.gz`, Ed25519, SHA-256, safe archive verification |
| Deployment | Docker Compose + versioned offline installation kit |

Структура репозитория:

```text
backend/    FastAPI, domain, DB, services, tests
frontend/   Vue 3 SPA
docs/       user/admin/architecture/protocol/security/component docs
deploy/     runtime deployment, offline kit tooling и qualification gates
data/       локальная runtime directory; generated content не коммитится
tools/      repository tooling, включая documentation checker
```

### Быстрый старт разработки

Требования:

- GNU Make;
- Python 3.12;
- Node.js 22+ и npm;
- Docker + Docker Compose v2 для container runtime.

Создайте локальную конфигурацию:

```bash
cp .env.example .env
```

Замените placeholders безопасными локальными значениями, прежде всего `JWT_SECRET`, runtime contour и local Harbor configuration. Реальные secrets не коммитьте.

Backend:

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e './backend[dev]'
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Из корня репозитория:

```bash
make lint-backend
make test-backend
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

Проверки frontend:

```bash
npm run lint
npm run typecheck
npm test
npm run build
```

Docker Compose для development/runtime:

```bash
make compose-config
make up
```

Portal по умолчанию публикуется через frontend на `http://localhost:${PORTAL_HTTP_PORT:-8080}`. Development/runtime Compose подробно описан в [deploy/README.md](deploy/README.md); он не заменяет offline release installation procedure.

## Проверки и CI

Запускайте минимально достаточный gate для затронутого поведения:

```bash
make docs-check
make lint-backend
make test-backend
make test-frontend
make smoke-compose
```

GitHub Actions использует path-aware selection и единый `quality-gate`. Docs-only change должен запускать documentation gate без несвязанных тяжёлых runtime jobs. Release-sensitive изменения дополнительно проходят clean-host и isolated SOURCE → TARGET qualification согласно CI scope.

Documentation gate проверяет repository-relative Markdown links без network crawling внешних сайтов.

Полная test policy: [docs/testing.md](docs/testing.md).

## Документация для разработки и сопровождения

- [docs/README.md](docs/README.md) — карта документов и приоритет источников;
- [docs/architecture.md](docs/architecture.md) — архитектура и current implementation state;
- [docs/frontend.md](docs/frontend.md) — frontend architecture и transfer UI;
- [docs/export-orchestration.md](docs/export-orchestration.md) — SOURCE export contract;
- [docs/import-orchestration.md](docs/import-orchestration.md) — TARGET import contract;
- [docs/operation-manager.md](docs/operation-manager.md) — background execution;
- [docs/testing.md](docs/testing.md) — scoped tests и CI;
- [docs/decisions.md](docs/decisions.md) — ADR registry;
- [CONTRIBUTING.md](CONTRIBUTING.md) — правила изменений.

`docs/harbor-transfer-portal.md` сохранён только как **исторический product/design reference** и не определяет current runtime, protocol или security behavior.

Документация проекта ведётся на русском языке; API fields, environment variables, enum, paths, CLI и другие технические identifiers сохраняются в исходном виде. При изменении поведения documentation impact обновляется в той же итерации.

## Release

Release notes: [docs/release-notes-v1.0.0.md](docs/release-notes-v1.0.0.md). Changelog: [CHANGELOG.md](CHANGELOG.md).

## Лицензия

Проект распространяется по лицензии [Apache License 2.0](LICENSE).
