# Архитектура Harbor Transfer Portal

**Статус:** актуальное архитектурное описание текущей ветки разработки v1.

Этот документ описывает действующие архитектурные границы Harbor Transfer Portal и явно отделяет уже реализованные компоненты от запланированных пользовательских потоков. Нормативные контракты не переопределяются здесь: формат переносимого пакета определяется [Offline Bundle Protocol v1](offline-bundle-v1.md), значимые решения — [ADR](decisions.md).

## 1. Назначение и ограничения

Harbor Transfer Portal предназначен для офлайн-передачи контейнерных образов и Helm OCI-чартов между двумя физически и сетево изолированными контурами.

Основное ограничение системы:

> между SOURCE и TARGET отсутствует прямой сетевой путь, и архитектура не должна создавать такой путь неявно.

Поэтому продукт разворачивается как **две независимые установки**:

- `SOURCE` работает только со своим локальным Harbor и создаёт переносимый пакет;
- `TARGET` работает только со своим локальным Harbor и проверяет/импортирует полученный пакет;
- SOURCE не хранит credentials TARGET;
- TARGET не хранит credentials SOURCE;
- прямая Harbor-to-Harbor replication через границу изоляции не используется.

Физический перенос файла между контурами находится вне сетевой архитектуры приложения и выполняется по правилам организации.

## 2. Контекст системы

```text
┌──────────────────────── SOURCE ────────────────────────┐
│                                                        │
│  Browser → Nginx/Vue → FastAPI → Harbor REST API       │
│                         │        → Skopeo              │
│                         │        → Helm OCI            │
│                         │                              │
│                         └→ BundlePackageService        │
│                                  │                     │
│                                  ▼                     │
│                     signed .htp.tar.gz + .sha256      │
└──────────────────────────────────┬─────────────────────┘
                                   │
                         физический перенос
                                   │
┌──────────────────────── TARGET ──▼─────────────────────┐
│                                                        │
│  Browser → Nginx/Vue → FastAPI → Bundle verifier       │
│                         │        → Harbor REST API      │
│                         │        → Skopeo              │
│                         │        → Helm OCI            │
│                         ▼                              │
│                    local Harbor                        │
└────────────────────────────────────────────────────────┘
```

На текущем этапе v1 реализованы инфраструктурные и protocol-critical primitives, но полный SOURCE export orchestration и TARGET import orchestration ещё развиваются. Наличие сервиса или UI-маршрута не означает автоматически, что весь пользовательский end-to-end сценарий завершён.

## 3. Runtime deployment

Текущий Docker Compose runtime содержит два сервиса.

| Компонент | Ответственность |
|---|---|
| `frontend` | Nginx, собранный Vue SPA, same-origin reverse proxy `/api/` на backend |
| `backend` | FastAPI, доменная логика, SQLite access, Harbor client, Skopeo, Helm и Bundle service |

Backend не публикуется напрямую на host в штатной Compose-топологии. Frontend публикует HTTP-порт и проксирует API во внутреннюю Compose network.

Обе роли `SOURCE` и `TARGET` используют те же application images. Поведение конкретной установки определяется `PORTAL_CONTOUR=SOURCE|TARGET` и только локальной конфигурацией Harbor.

Подробности развертывания находятся в [deploy/README.md](../deploy/README.md).

## 4. Основные слои backend

Текущий backend разделён по ответственности:

```text
backend/app/
├── api/       HTTP endpoints и transport-level validation
├── auth/      authentication/authorization helpers
├── db/        SQLAlchemy models, repositories, session
├── domain/    Bundle/Protocol/Operations/Receipt domain contracts
├── schemas/   API request/response schemas
├── services/  integrations и application services
├── utils/     общие технические helpers
├── config.py  runtime settings
└── main.py    FastAPI application assembly
```

### 4.1. API layer

В текущем `main` существуют API-модули для:

- authentication;
- health/readiness;
- users;
- Harbor browse/integration;
- Harbor/settings administration.

Export/import orchestration endpoints добавляются отдельными задачами и не должны считаться существующими только потому, что в frontend уже зарезервированы соответствующие маршруты.

### 4.2. Domain layer

`backend/app/domain/` содержит protocol/domain types, которые не должны зависеть от UI или конкретного subprocess transport.

Основные области:

- Bundle manifest и artifact descriptors;
- protocol validation helpers;
- export/import operation state transitions;
- receipt model.

Нормативная JSON Schema хранится в `docs/schema/` и синхронизируется с typed backend model.

### 4.3. Service layer

Текущие сервисы имеют отдельные границы:

| Сервис | Ответственность |
|---|---|
| `harbor_client.py` | работа с Harbor REST API локального контура |
| `harbor_settings.py` | разрешение effective Harbor configuration и managed settings |
| `skopeo_service.py` | container image transport через структурированный subprocess argv |
| `helm_oci_service.py` | Helm OCI pull/push и безопасная работа subprocess/workspace |
| `bundle_package_service.py` | build/verify/publish/extract Offline Bundle v1 |

Такое разделение принципиально: Harbor REST API отвечает за registry metadata/control plane, а перенос payload делегируется специализированным OCI/Helm инструментам. Backend не переimplementирует container registry copy protocol самостоятельно.

## 5. Ответственность Harbor REST API, Skopeo, Helm и Bundle service

### Harbor REST API

Используется для работы с локальным Harbor: проверки соединения, browse metadata и операций, которым нужен registry control-plane контекст.

Harbor client не должен использоваться как скрытый канал между SOURCE и TARGET.

### Skopeo

Используется для переноса container image payload между локальным Harbor и filesystem representation. В Bundle Protocol v1 container payload представлен как OCI image-layout согласно [ADR-009](adr/ADR-009-oci-layout-payload.md).

Недоверенные значения передаются subprocess только как структурированные аргументы, без shell-конкатенации.

### Helm OCI

Используется для pull/push Helm charts, опубликованных как OCI artifacts в локальном Harbor. Переносимый chart payload внутри bundle — `.tgz`.

Helm runtime workspace/config/cache изолируются от пользовательских путей и общего host environment согласно реализации сервиса.

### BundlePackageService

Это protocol-critical boundary между подготовленными payload и переносимым файлом доставки.

На SOURCE service:

1. snapshot-копирует разрешённые payload;
2. вычисляет checksums;
3. создаёт canonical `manifest.json`;
4. подписывает manifest Ed25519 private key;
5. создаёт deterministic `.htp.tar.gz`;
6. проверяет созданный archive тем же verifier path;
7. атомарно публикует archive;
8. после archive публикует `.sha256` readiness sidecar.

На TARGET verifier до controlled extraction проверяет archive limits/path safety, schema, canonical manifest, signature, checksum set и payload metadata.

Точные правила определяет [Offline Bundle Protocol v1](offline-bundle-v1.md), а реализационные детали описаны в [package-service.md](package-service.md).

## 6. Offline Bundle как граница совместимости

SOURCE и TARGET не разделяют runtime state. Их совместимость определяется переносимым protocol contract.

Обязательные security-critical элементы Bundle v1:

```text
manifest.json
manifest.sig
checksums.sha256
images/...
charts/...
```

`manifest.sig` подтверждает authenticity canonical manifest, а SHA-256 checksums — transport integrity payload. Checksum не заменяет подпись.

Изменение несовместимой семантики требует отдельного protocol/ADR решения; архитектурный документ не может незаметно изменить Bundle contract.

## 7. Data flow: SOURCE export

Целевой end-to-end SOURCE flow:

```text
Operator
  → Frontend
  → Export API/orchestrator
  → Harbor metadata resolution
  → Skopeo / Helm payload export
  → BundlePackageService build + self-verify
  → outgoing bundle + .sha256
  → download / physical transfer
```

### Текущий статус

На текущем `main` уже реализованы Harbor integration, Skopeo service, Helm OCI service и BundlePackageService. Полная orchestration API/background operation chain и законченный export wizard являются последующими задачами v1.

Поэтому прямой вызов отдельных primitives разработчиком не следует документировать как штатный пользовательский процесс.

## 8. Data flow: TARGET import

Целевой TARGET flow:

```text
Physical bundle
  → controlled intake
  → Bundle verifier
  → signature/checksum/schema validation
  → conflict preview against local Harbor
  → import orchestration
  → Skopeo / Helm
  → target digest/result verification
  → persisted receipt/history
```

Registry mutation допускается только после protocol/security verification.

### Текущий статус

Bundle verifier/extraction primitives существуют. Полная intake/preview/conflict/import orchestration и законченный import UI ещё не являются завершённым v1 end-to-end потоком.

## 9. Operation state model

Состояния являются доменными данными, а не результатом парсинга логов.

### Export

```text
CREATED
  → VALIDATING
  → RUNNING
  → PACKAGING
  → VERIFYING
  → COMPLETED
```

Из активных состояний возможны предусмотренные переходы в `FAILED` или `CANCELLED`.

### Import

```text
UPLOADED | DISCOVERED
  → VERIFYING
  → READY
  → IMPORTING
  → VERIFYING_TARGET
  → COMPLETED
```

Для import также используются терминальные `FAILED`, `REJECTED`, `CANCELLED` согласно legal transitions в domain model.

Терминальное состояние не может переходить дальше. Illegal transition является доменной ошибкой.

Пер-артефактные статусы и точная семантика определены в [Offline Bundle Protocol v1](offline-bundle-v1.md).

## 10. Persistence

Базовая v1 persistence — SQLite + SQLAlchemy + Alembic.

Compose монтирует named volume `portal-data` в `/app/data`. Текущая эксплуатационная структура включает:

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

Не все каталоги одинаково критичны для backup. SQLite, managed secrets/CA, signing/trust key material и необходимые history/receipt metadata должны рассматриваться как разные классы данных с разными требованиями доступа.

Точные backup/restore и release procedures будут закреплены в admin/release документации; их нельзя выводить только из этой схемы каталогов.

## 11. Configuration and secrets boundary

Каждая установка имеет только один effective local Harbor configuration.

В соответствии с [ADR-005](adr/ADR-005-harbor-secrets-tls.md):

- non-secret Harbor overrides могут храниться в SQLite;
- managed credential хранится file-backed и не возвращается через API;
- deployment `HARBOR_PASSWORD_FILE`/environment остаются bootstrap fallback;
- managed custom CA сохраняется server-side после validation;
- TLS verification включена по умолчанию;
- SOURCE private signing key существует только на SOURCE;
- TARGET хранит только trusted SOURCE public keys;
- secret/key values не должны попадать в bundle, frontend state, audit metadata или обычные API responses.

## 12. Trust boundaries

Ключевые границы доверия:

1. **Browser ↔ portal API** — authentication/RBAC boundary.
2. **Portal ↔ local Harbor** — credential/TLS boundary; только Harbor текущего контура.
3. **Backend ↔ Skopeo/Helm subprocess** — untrusted input/argv/environment/workspace boundary.
4. **SOURCE ↔ physical bundle** — signing/integrity/publication boundary.
5. **Physical bundle ↔ TARGET** — полностью недоверенный вход до завершения verifier checks.
6. **Backend persistent volume** — host/deployment boundary для DB, secrets и keys.

Более полная threat model поддерживается в [security.md](security.md); до завершения задачи #56 этот документ следует читать вместе с Bundle Protocol и ADR-005.

## 13. Frontend architecture

Frontend использует Vue 3 + Vite + TypeScript, Vue Router, Pinia, Axios, Element Plus и Lucide.

Основной источник runtime contour identity — local backend `GET /api/health`; frontend не должен hardcode SOURCE/TARGET в конкретных страницах.

Маршруты `/export`, `/import`, `/history` уже зарезервированы в frontend architecture, но часть соответствующих views на текущем этапе является scaffold/placeholder. Фактическую готовность функции необходимо определять по реализации и issue state, а не по наличию route.

Подробности frontend находятся в [frontend.md](frontend.md).

## 14. Observability and audit

Health/readiness endpoints существуют и не должны раскрывать secret configuration.

Полноценная operation history, audit model, structured logging/correlation и report lifecycle развиваются отдельными задачами v1. Архитектура требует, чтобы UI получал структурированное operation state и persisted results, а не определял успех по тексту subprocess/container logs.

## 15. Offline runtime и release boundary

Текущий Docker build может использовать внешние package/image repositories на build stage. Это не означает допустимость internet dependency в закрытом runtime.

Архитектурное правило:

- online build environment может получать зависимости согласно release pipeline;
- offline installation должна загружать заранее собранные проверенные images/artifacts;
- штатный runtime после загрузки images не должен обращаться к internet/CDN для запуска приложения.

Финальный offline installation kit и acceptance E2E относятся к задаче #28 и не считаются готовыми только на основании рабочего development Compose.

## 16. Текущее состояние реализации

| Область | Статус в текущем `main` |
|---|---|
| FastAPI foundation / health | реализовано |
| Authentication / users / RBAC foundation | реализовано |
| Harbor settings / credential / CA management | реализовано |
| Harbor REST browse/integration foundation | реализовано |
| Skopeo transfer service | реализовано как service primitive |
| Helm OCI service | реализовано как service primitive |
| Offline Bundle v1 typed protocol/schema | реализовано |
| Bundle build/sign/verify/safe extraction | реализовано |
| Vue application shell/login/settings foundation | реализовано |
| Export orchestration API/background flow | в разработке |
| Export wizard | scaffold / в разработке |
| TARGET import orchestration | в разработке |
| Import wizard | scaffold / в разработке |
| Full operation history/audit/report UX | в разработке |
| Final offline release kit / acceptance E2E | запланировано в #28 |

Эта таблица описывает состояние на момент обновления документа и должна изменяться вместе с соответствующей реализацией.

## 17. Источники истины

Используйте документы по назначению:

| Область | Authoritative source |
|---|---|
| Текущая архитектура | этот `docs/architecture.md` |
| Bundle Protocol v1 | [offline-bundle-v1.md](offline-bundle-v1.md) + JSON Schema |
| Package build/verify implementation boundary | [package-service.md](package-service.md) |
| Архитектурные решения | [decisions.md](decisions.md) и `docs/adr/` |
| Deployment/runtime | [deploy/README.md](../deploy/README.md) |
| Testing/CI | [testing.md](testing.md) |
| Frontend structure | [frontend.md](frontend.md) |
| Общий security overview | [security.md](security.md), Bundle Protocol и security ADR |

## 18. Исторический design document

`docs/harbor-transfer-portal.md` был исходным объединённым документом постановки задачи, UI-концепции, ранних примеров и плана разработки. Он полезен как **исторический product/design reference**, но не является нормативным описанием текущего protocol/runtime поведения.

Если исторический документ противоречит:

1. текущему коду;
2. принятому ADR;
3. `docs/offline-bundle-v1.md` / JSON Schema;
4. этому архитектурному документу;

следует использовать более актуальный специализированный источник, а расхождение оформить как documentation issue.

## 19. Правило обновления

При изменении архитектурной границы в той же итерации необходимо определить documentation impact:

- новый/изменённый protocol contract → protocol doc/schema/ADR;
- новый service boundary → architecture + specialized service doc;
- новое secret/trust решение → security + ADR + deployment;
- новый persisted state → domain/architecture/history docs;
- завершённый пользовательский flow → обновить статус здесь и соответствующий user guide.

Документация не должна описывать планируемую функцию как уже доступную пользователю.