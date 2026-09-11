# Harbor Transfer Portal

![Harbor Transfer Portal](docs/img/devops-logo-white.png)

[![CI](https://github.com/askarahodov/Harbor-Transfer-Portal/actions/workflows/ci.yml/badge.svg)](https://github.com/askarahodov/Harbor-Transfer-Portal/actions/workflows/ci.yml)

Harbor Transfer Portal — локальный веб-портал для безопасной офлайн-передачи container images и Helm OCI charts между двумя физически и сетево изолированными Harbor-контурами.

> **Статус:** активная разработка **v1**. Репозиторий уже содержит protocol/security/transfer foundations, но полный SOURCE→TARGET пользовательский flow и финальный offline installation kit ещё развиваются. Текущий `main` не следует автоматически считать готовым production-релизом.

## За 30 секунд

Портал устанавливается отдельно в двух контурах:

```text
Harbor SOURCE
    ↓
Portal SOURCE
    ↓
подписанный Offline Bundle v1
(.htp.tar.gz + .sha256)
    ↓
разрешённый физический носитель
    ↓
Portal TARGET
    ↓
проверка подписи / checksum / структуры / policy
    ↓
Harbor TARGET
```

Между SOURCE и TARGET **нет прямого сетевого соединения**. Каждая установка знает только свой локальный Harbor; credentials противоположного контура не хранятся.

## Что уже есть

| Область | Текущий статус |
|---|---|
| FastAPI foundation, health/readiness | реализовано |
| Local users, JWT, RBAC | реализовано |
| Harbor settings, managed credential и custom CA | реализовано |
| Harbor REST browse/integration foundation | реализовано |
| Skopeo container transfer service | реализовано как service primitive |
| Helm OCI service | реализовано как service primitive |
| Offline Bundle Protocol v1 + JSON Schema | реализовано |
| Bundle build/sign/verify/safe extraction | реализовано |
| Persistent `OperationManager`, progress/cancel/restart reconciliation | реализовано |
| Vue shell/login/settings foundation | реализовано |
| SOURCE export feature-specific orchestration/UI | в разработке |
| TARGET intake/import feature-specific orchestration/UI | в разработке |
| Full history/audit/report UX | в разработке |
| Final offline installer + acceptance E2E | запланировано |

Подробная и более точная таблица current state поддерживается в [архитектурной документации](docs/architecture.md).

## Ключевые инварианты

- SOURCE и TARGET работают независимо и не соединяются portal-to-portal или Harbor-to-Harbor.
- Runtime закрытого контура не должен зависеть от internet/CDN.
- Offline Bundle v1 содержит canonical `manifest.json`, Ed25519 `manifest.sig` и SHA-256 integrity metadata.
- SHA-256 проверяет целостность, но не заменяет цифровую подпись.
- Полученный TARGET bundle считается недоверенным до успешного verifier flow.
- TLS verification включена по умолчанию; private CA поддерживается явно.
- Harbor credentials и signing private keys не хранятся в Git и не включаются в bundle.
- Skopeo/Helm запускаются через структурированный subprocess argv без shell-конкатенации пользовательского ввода.
- Conflict не должен приводить к неявной перезаписи target artifact.
- Operation status — persisted domain state, а не вывод из текста логов.

Полная модель угроз и доверия: [docs/security.md](docs/security.md).

## Кому куда идти

| Если вы… | Начните здесь |
|---|---|
| хотите понять, что это за продукт | [Паспорт проекта](docs/project-passport.md) |
| хотите увидеть всю карту документации | [docs/README.md](docs/README.md) |
| администрируете установку, Harbor credentials/CA, keys или backup | [Admin Guide](docs/admin-guide.md) |
| настраиваете development/runtime Compose | [Deployment](deploy/README.md) |
| проектируете/разрабатываете backend или интеграции | [Архитектура](docs/architecture.md) |
| реализуете совместимость SOURCE/TARGET | [Offline Bundle Protocol v1](docs/offline-bundle-v1.md) |
| разбираете security/trust boundaries | [Security](docs/security.md) |
| меняете background execution | [OperationManager](docs/operation-manager.md) |
| работаете с Skopeo/Helm | [Skopeo](docs/skopeo-service.md) / [Helm OCI](docs/helm-oci-service.md) |
| меняете frontend | [Frontend](docs/frontend.md) |
| меняете CI/tests | [Testing/CI](docs/testing.md) |
| принимаете архитектурное решение | [ADR registry](docs/decisions.md) |
| собираетесь внести изменение | [CONTRIBUTING.md](CONTRIBUTING.md) |

`docs/harbor-transfer-portal.md` сохранён как **исторический product/design reference**. Он не является текущим нормативным source для protocol/runtime/security решений.

## Архитектура и стек

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
| Deployment | Docker Compose |

Структура репозитория:

```text
backend/    FastAPI, domain, DB, services, tests
frontend/   Vue 3 SPA
docs/       architecture, protocol, security, component и project docs
deploy/     Compose/runtime deployment и smoke checks
data/       локальная runtime directory; generated content не коммитится
tools/      repository tooling, включая documentation checker
```

## Быстрый старт разработки

Базовые требования:

- GNU Make;
- Python 3.12;
- Node.js 22+ и npm;
- Docker + Docker Compose v2 для container runtime.

Создайте локальную конфигурацию:

```bash
cp .env.example .env
```

Замените placeholders безопасными локальными значениями, прежде всего `JWT_SECRET`, contour и local Harbor configuration. Реальные secrets не коммитьте.

### Backend

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

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Проверки:

```bash
npm run lint
npm run typecheck
npm test
npm run build
```

### Docker Compose

```bash
make compose-config
make up
```

Portal по умолчанию публикуется через frontend на `http://localhost:${PORTAL_HTTP_PORT:-8080}`. Подробности bootstrap admin, Harbor credential/CA, keys, persistent volume и smoke checks находятся в [Admin Guide](docs/admin-guide.md) и [deploy/README.md](deploy/README.md).

## SOURCE и TARGET configuration

`PORTAL_CONTOUR` принимает только:

```text
SOURCE
TARGET
```

Один экземпляр не является одновременно SOURCE и TARGET.

Каждая установка использует нейтральный набор `HARBOR_*` только для собственного локального Harbor. Предпочтительное постоянное хранение Harbor credential — managed/file-backed flow; `HARBOR_PASSWORD` остаётся bootstrap fallback, а не рекомендуемым постоянным production storage.

SOURCE хранит private Ed25519 signing key. TARGET хранит только trusted SOURCE public keys.

## Проверки и CI

Локально запускайте минимально достаточный gate для затронутого поведения:

```bash
make docs-check
make lint-backend
make test-backend
make test-frontend
make smoke-compose
```

GitHub Actions использует path-aware selection и единый `quality-gate`. Изменение самого workflow включает все реализованные области; обычный docs-only PR не должен запускать несвязанные тяжёлые runtime jobs.

Documentation gate проверяет repository-relative Markdown links без network crawling внешних сайтов.

Полная policy: [docs/testing.md](docs/testing.md).

## Разработка и документация

Человекоориентированная документация проекта ведётся на русском языке. API fields, environment variables, enum, paths, CLI и другие технические identifiers сохраняются в исходном виде.

При изменении поведения documentation impact обновляется в той же итерации. Нельзя описывать planned/scaffold функцию как уже доступную пользователю.

Правила для разработчиков и ИИ-агентов: [CONTRIBUTING.md](CONTRIBUTING.md).

## Roadmap v1

Ближайшие продуктовые milestones:

1. закончить SOURCE export orchestration;
2. закончить TARGET intake/preview/import orchestration;
3. подключить законченные export/import UI flows;
4. завершить history/audit/report user experience;
5. завершить user guide и troubleshooting по фактическим flows;
6. собрать финальный offline installation kit и выполнить SOURCE→TARGET acceptance E2E.

Актуальная детализация работ ведётся в GitHub Issues; README намеренно не дублирует issue backlog.

## Лицензия

Проект распространяется по лицензии [Apache License 2.0](LICENSE).