# Архитектура Harbor Transfer Portal

**Статус:** актуальное архитектурное описание текущей разработки v1.

Этот документ описывает действующие архитектурные границы Harbor Transfer Portal и отделяет уже реализованные компоненты от ещё незавершённых пользовательских потоков. Нормативные контракты здесь не переопределяются: формат переносимого пакета задаёт [Offline Bundle Protocol v1](offline-bundle-v1.md), значимые решения фиксируются в [ADR](decisions.md), а правила фонового выполнения — в [operation-manager.md](operation-manager.md).

## 1. Назначение и главное ограничение

Harbor Transfer Portal предназначен для офлайн-передачи контейнерных образов и Helm OCI-чартов между двумя физически и сетево изолированными контурами.

Главный архитектурный инвариант:

> между SOURCE и TARGET отсутствует прямой сетевой путь, и приложение не должно создавать такой путь неявно.

Поэтому используются две независимые установки:

- `SOURCE` взаимодействует только со своим локальным Harbor и создаёт переносимый пакет;
- `TARGET` взаимодействует только со своим локальным Harbor и проверяет/импортирует полученный пакет;
- SOURCE не хранит credentials TARGET;
- TARGET не хранит credentials SOURCE;
- прямая Harbor-to-Harbor replication через границу изоляции не используется.

Физический перенос файла находится вне сетевой архитектуры приложения и выполняется по организационной процедуре.

## 2. Контекст системы

```text
┌────────────────────────── SOURCE ──────────────────────────┐
│                                                           │
│ Browser → Nginx/Vue → FastAPI                             │
│                         │                                  │
│                         ├→ Harbor REST API                 │
│                         ├→ OperationManager                │
│                         │    ├→ Skopeo                     │
│                         │    ├→ Helm OCI                   │
│                         │    └→ BundlePackageService       │
│                         │                                  │
│                         └→ SQLite / persistent state       │
│                                      │                    │
│                                      ▼                    │
│                          signed .htp.tar.gz + .sha256      │
└──────────────────────────────────────┬────────────────────┘
                                       │
                             физический перенос
                                       │
┌────────────────────────── TARGET ────▼────────────────────┐
│                                                           │
│ Browser → Nginx/Vue → FastAPI                             │
│                         │                                  │
│                         ├→ Bundle verifier                 │
│                         ├→ OperationManager                │
│                         │    ├→ Skopeo                     │
│                         │    └→ Helm OCI                   │
│                         ├→ Harbor REST API                 │
│                         └→ SQLite / persistent state       │
│                                      │                    │
│                                      ▼                    │
│                                  local Harbor             │
└───────────────────────────────────────────────────────────┘
```

На текущем этапе реализованы protocol-critical transfer primitives, общий persistent background operation foundation и feature-specific backend SOURCE export orchestration. TARGET intake/import orchestration и завершённые пользовательские export/import UI продолжают развиваться. Наличие UI route само по себе не означает готовность пользовательского end-to-end сценария.

## 3. Runtime deployment

Текущий Docker Compose runtime содержит два сервиса.

| Компонент | Ответственность |
|---|---|
| `frontend` | Nginx, собранный Vue SPA, same-origin reverse proxy `/api/` на backend |
| `backend` | FastAPI, SQLite, Harbor client/settings, OperationManager, Skopeo, Helm и Bundle services |

Backend не публикуется напрямую на host в штатной Compose-топологии. Frontend публикует HTTP-порт и проксирует API во внутреннюю Compose network.

Обе роли `SOURCE` и `TARGET` используют те же application images. Роль конкретной установки задаётся `PORTAL_CONTOUR=SOURCE|TARGET`, а Harbor configuration относится только к локальному контуру.

Baseline v1 использует один backend instance и in-process `asyncio` OperationManager без Redis/Celery. Это осознанная текущая граница, а не гарантия горизонтального multi-instance execution.

Подробности развертывания: [deploy/README.md](../deploy/README.md).

## 4. Слои backend

```text
backend/app/
├── api/       HTTP endpoints и transport-level validation
├── auth/      authentication/authorization helpers
├── db/        SQLAlchemy models, repositories, session
├── domain/    Bundle/Protocol/Operations/Receipt contracts
├── schemas/   API request/response schemas
├── services/  application/integration services
├── utils/     технические helpers
├── config.py  runtime settings
└── main.py    FastAPI application assembly/lifecycle
```

### 4.1. API layer

В текущем `main` существуют API для:

- authentication;
- health/readiness;
- users;
- Harbor browse/integration;
- Harbor/settings administration;
- SOURCE export preview/start: `POST /api/exports/preview`, `POST /api/exports`;
- metadata/download завершённого SOURCE delivery: `GET /api/exports/{id}/bundle`, `GET /api/exports/{id}/download`;
- чтения persisted operation state: `GET /api/operations/{id}`;
- отмены операции по RBAC: `POST /api/operations/{id}/cancel`.

Feature-specific SOURCE export request contract реализован. TARGET intake/import request contracts остаются ответственностью #19. Generic operation polling/cancel API является общей execution boundary для обоих направлений.

### 4.2. Domain layer

`backend/app/domain/` содержит типы и state machine, независимые от UI и subprocess transport.

Основные области:

- Bundle manifest и artifact descriptors;
- protocol validation helpers;
- export/import operation states и legal transitions;
- per-artifact result states;
- receipt model.

Нормативные Bundle schemas находятся в `docs/schema/` и должны оставаться согласованными с typed backend model.

### 4.3. Service layer

| Сервис | Ответственность |
|---|---|
| `harbor_client.py` | Harbor REST API локального контура |
| `harbor_settings.py` | effective Harbor configuration и managed credential/CA |
| `skopeo_service.py` | container image transport через структурированный subprocess argv |
| `helm_oci_service.py` | Helm OCI pull/push и безопасный workspace/subprocess |
| `bundle_package_service.py` | build/verify/publish/extract Offline Bundle v1 |
| `operation_manager.py` | persistent background execution, progress, cancellation, worker ownership, restart reconciliation |
| `export_orchestrator.py` | SOURCE selection validation, Skopeo/Helm orchestration, fail-fast packaging и bundle metadata |
| `export_recovery.py` | startup cleanup незавершённых export publications |

Harbor REST API отвечает за registry control plane/metadata, а payload transport делегируется Skopeo и Helm. Backend не переimplementирует registry copy protocol самостоятельно.

## 5. OperationManager как execution boundary

`OperationManager` выполняет длительную работу вне HTTP request lifetime и сохраняет наблюдаемое состояние в SQLite.

Основные гарантии текущего v1 foundation:

- `Operation` создаётся и коммитится до запуска background task;
- worker атомарно захватывает operation через persistent `worker_token`;
- progress/state/artifact updates используют отдельные короткие DB sessions;
- одновременно выполняемых операций не больше `OPERATION_MAX_CONCURRENT`;
- каждая операция получает server-generated private workspace `operation-<id>`;
- перед крупной работой может применяться disk reserve preflight;
- progress структурированный и не выдумывает ETA/процент, если таких данных нет;
- expected worker failures сохраняют стабильный safe error code/message;
- raw exception/subprocess output и secrets не становятся operation API payload;
- cancellation сохраняется как `cancel_requested_at` и передаётся в локальную task/subprocess cancellation chain.

Подробный контракт: [Менеджер фоновых операций](operation-manager.md).

### Restart/recovery v1

Resume середины Skopeo/Helm-команды не поддерживается.

После неожиданного рестарта active execution states:

```text
VALIDATING
RUNNING
PACKAGING
VERIFYING
IMPORTING
VERIFYING_TARGET
```

переводятся в `FAILED` с безопасным кодом `operation_interrupted_restart`; активные artifact rows также завершаются ошибкой, временный workspace очищается.

Wait states:

```text
CREATED
UPLOADED
DISCOVERED
READY
```

не считаются уже выполняемой registry mutation. Stale worker claim освобождается. Workspace состояния `READY` может быть сохранён для будущего import orchestration.

Для SOURCE export перед запуском OperationManager выполняется дополнительная reconciliation publication boundary: archive/sidecar, относящиеся к export operation, которая не достигла `COMPLETED`, удаляются, а неполная persisted bundle metadata очищается. Это закрывает crash-window после физической публикации `.sha256`, но до terminal commit операции.

Это означает **reconciliation, а не transparent resume**.

## 6. Ответственность transfer services

### Harbor REST API

Используется только с локальным Harbor: connection/system info, browse metadata и control-plane операции. Harbor client не является каналом между SOURCE и TARGET.

### Skopeo

Используется для container image payload. Bundle v1 хранит контейнерное содержимое как OCI image-layout согласно [ADR-009](adr/ADR-009-oci-layout-payload.md).

Недоверенные значения передаются subprocess структурированными argv без shell-конкатенации. Успешный exit code без digest verification не считается полным доказательством успеха.

Подробности: [skopeo-service.md](skopeo-service.md).

### Helm OCI

Используется для pull/push OCI Helm charts в локальном Harbor. Bundle содержит chart payload как `.tgz` с отдельной checksum/metadata validation.

Подробности: [helm-oci-service.md](helm-oci-service.md).

### BundlePackageService

Protocol-critical boundary между подготовленными payload и переносимым delivery file.

На SOURCE service:

1. получает/копирует подготовленные payload в контролируемый workspace;
2. вычисляет checksums;
3. создаёт canonical `manifest.json`;
4. подписывает manifest Ed25519 private key;
5. создаёт deterministic `.htp.tar.gz`;
6. self-verifies созданный archive;
7. атомарно публикует archive;
8. только после archive публикует `.sha256` readiness sidecar.

TARGET verifier до import проверяет archive safety/limits, schema/canonical manifest, signature, checksum set и signed payload metadata.

Нормативные правила: [Offline Bundle Protocol v1](offline-bundle-v1.md). Реализационная граница: [package-service.md](package-service.md).

## 7. Offline Bundle как граница совместимости

SOURCE и TARGET не разделяют runtime state. Их совместимость определяется версионированным переносимым contract.

Security-critical top-level элементы Bundle v1:

```text
manifest.json
manifest.sig
checksums.sha256
images/...
charts/...
```

`manifest.sig` подтверждает authenticity canonical manifest, а SHA-256 checksums контролируют integrity payload. Checksum не заменяет подпись.

Несовместимое изменение contract требует protocol/schema/ADR решения; prose в architecture не может незаметно менять Bundle semantics.

## 8. SOURCE export flow

Текущий backend flow:

```text
Operator/Admin
  → Export API/orchestrator
  → authoritative Harbor selection validation
  → create persisted Operation + artifact rows
  → OperationManager
  → repeated Harbor metadata/digest validation
  → Skopeo / Helm payload export
  → BundlePackageService build + self-verify
  → atomic outgoing bundle + .sha256
  → persisted bundle filename / size / SHA-256
  → COMPLETED
  → owner/admin download / physical transfer
```

### Текущий статус

Feature-specific backend SOURCE export orchestration реализован и покрыт regression tests.

Реализовано:

- `operator|admin` preview/start с SOURCE contour guard;
- stable selection `project/repository/reference/digest` и повторная authoritative Harbor validation;
- обнаружение digest/type drift между выбором и worker execution;
- fail-fast mixed image + Helm delivery;
- background execution через `OperationManager`;
- bundle build/sign/self-verify и atomic publication;
- cancellation barrier во время blocking packaging;
- cleanup publication при failure/cancel/restart до `COMPLETED`;
- persisted bundle filename/size/SHA-256;
- owner/admin metadata и disk-backed download.

Ещё не следует считать завершёнными:

- законченный export wizard/frontend flow (#18);
- полный пользовательский SOURCE acceptance через UI;
- сквозной SOURCE→physical→TARGET acceptance до завершения #19/#28.

Подробный component/API contract: [export-orchestration.md](export-orchestration.md).

## 9. TARGET import flow

Целевой flow:

```text
Physical bundle
  → controlled intake
  → create/discover persisted Operation
  → Bundle verifier
  → signature/checksum/schema validation
  → READY / preview
  → conflict policy
  → OperationManager import worker
  → Skopeo / Helm
  → target verification
  → receipt/history
```

Registry mutation допускается только после package verification.

### Текущий статус

Bundle verifier и generic OperationManager foundation реализованы. Полная intake/preview/conflict/import orchestration, feature-specific import API и законченный import wizard ещё в разработке.

## 10. Operation state model

Состояние операции — persisted domain data, а не результат парсинга логов.

### Export baseline

```text
CREATED
  → VALIDATING
  → RUNNING
  → PACKAGING
  → VERIFYING
  → COMPLETED
```

Из initial/active states допускаются определённые state machine переходы в `FAILED`/`CANCELLED`, в том числе preflight/worker failure.

### Import baseline

```text
UPLOADED | DISCOVERED
  → VERIFYING
  → READY
  → IMPORTING
  → VERIFYING_TARGET
  → COMPLETED
```

Для import также используются `FAILED`, `REJECTED`, `CANCELLED` согласно legal transitions.

Terminal state дальше не переходит. Illegal transition является доменной ошибкой.

## 11. Persisted operation progress и cancellation

`GET /api/operations/{id}` возвращает persisted structured state, включая текущий status/phase, artifact counters, running artifact ids и безопасные error fields. Для завершённого SOURCE export response также проецирует persisted bundle filename/size/SHA-256.

API не возвращает `worker_token`, raw subprocess logs или выдуманный ETA.

`POST /api/operations/{id}/cancel` разрешён:

- `admin` — для любой операции;
- `operator` — для собственной;
- `viewer` — запрещено.

Frontend route guard не заменяет backend RBAC.

## 12. Persistence

Базовая v1 persistence — SQLite + SQLAlchemy + Alembic.

Compose монтирует `portal-data` в `/app/data`. Основные классы данных:

```text
/app/data/
├── harbor-transfer-portal.db
├── packages/
├── incoming/
├── outgoing/
├── receipts/
├── logs/
├── secrets/
├── keys/
└── tmp/
    └── operations/
```

SQLite содержит operation state, worker ownership/cancellation metadata, completed export bundle metadata и другую application metadata. `tmp/operations` используется для private operation workspaces и не является заменой persisted DB state.

SQLite, managed secrets/CA, signing/trust key material, receipts/history и retained packages имеют разные backup/retention требования.

## 13. Configuration and secrets boundary

Каждая установка имеет один effective local Harbor configuration.

Согласно [ADR-005](adr/ADR-005-harbor-secrets-tls.md):

- non-secret Harbor overrides могут храниться в SQLite;
- managed Harbor credential хранится file-backed и не возвращается API;
- `HARBOR_PASSWORD_FILE`/environment остаются bootstrap fallback;
- managed custom CA сохраняется server-side после validation;
- TLS verification включена по умолчанию;
- SOURCE private signing key существует только на SOURCE;
- TARGET хранит только trusted SOURCE public keys;
- secret/key values не должны попадать в bundle, frontend state, operation errors, audit metadata или обычные API responses.

OperationManager-specific runtime settings включают:

- `OPERATION_WORKSPACE_ROOT`;
- `OPERATION_MAX_CONCURRENT`;
- `OPERATION_DISK_RESERVE_BYTES`;
- `OPERATION_SHUTDOWN_TIMEOUT_SECONDS`.

## 14. Trust boundaries

Ключевые границы доверия:

1. **Browser ↔ Portal API** — authentication/RBAC.
2. **Portal ↔ local Harbor** — credential/TLS; только Harbor текущего контура.
3. **Backend ↔ Skopeo/Helm subprocess** — argv/environment/workspace и недоверенный input.
4. **HTTP request ↔ background task** — request-scoped DB session не передаётся worker; состояние сохраняется явно.
5. **OperationManager ↔ persistent state** — worker ownership/cancellation/restart reconciliation.
6. **SOURCE ↔ physical bundle** — signing/integrity/atomic publication.
7. **Physical bundle ↔ TARGET** — полностью недоверенный input до verifier checks.
8. **Backend persistent volume ↔ host** — DB/secrets/keys filesystem boundary.

Полная threat model: [security.md](security.md).

## 15. Frontend architecture

Frontend использует Vue 3 + Vite + TypeScript, Vue Router, Pinia, Axios, Element Plus и Lucide.

Runtime contour identity берётся из local backend `GET /api/health`; отдельные views не должны hardcode SOURCE/TARGET.

Маршруты `/export`, `/import`, `/history` могут существовать до завершения соответствующего end-to-end flow. Фактическую готовность функции определяют implementation/current-state docs и tests, а не наличие route.

Операционные UI должны использовать structured operation API/state, а не разбирать текст subprocess logs.

Подробности: [frontend.md](frontend.md).

## 16. Observability, audit и reports

Health/readiness endpoints не должны раскрывать secret configuration.

Generic persisted operation progress/cancellation API уже реализован. SOURCE export добавляет persisted delivery filename/size/SHA-256 только после verified publication. Полный history/audit/report UX и release-grade correlation продолжают развиваться отдельными v1 задачами.

Источником истины о статусе операции является persisted domain state. Логи остаются диагностическим каналом, а не механизмом определения `COMPLETED`.

## 17. Offline runtime и release boundary

Controlled build/release environment может использовать внешние package/image repositories на build stage. Закрытый runtime после доставки готовых images не должен зависеть от internet/CDN.

Финальный offline installation kit и acceptance E2E относятся к #28 и не считаются готовыми на основании working development Compose.

## 18. Текущее состояние реализации

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
| Persistent generic OperationManager | реализовано |
| Operation polling / cancellation API | реализовано |
| Worker claim / concurrency / disk preflight | реализовано |
| Restart reconciliation | реализовано; mid-command resume не поддерживается |
| SOURCE export feature-specific backend orchestration/API | реализовано |
| SOURCE export bundle metadata/download | реализовано |
| Vue shell/login/settings foundation | реализовано |
| Export wizard | scaffold / в разработке |
| TARGET intake/import feature-specific orchestration | в разработке |
| Import wizard | scaffold / в разработке |
| Full history/audit/report UX | в разработке |
| Final offline release kit / acceptance E2E | запланировано в #28 |

Generic OperationManager foundation предоставляет execution/lifecycle primitives; SOURCE export orchestration уже использует их как feature-specific worker flow, TARGET import orchestration ещё предстоит реализовать.

## 19. Источники истины

| Область | Authoritative source |
|---|---|
| Текущая архитектура/current state | этот `docs/architecture.md` |
| SOURCE export orchestration/API | [export-orchestration.md](export-orchestration.md) |
| Bundle Protocol v1 | [offline-bundle-v1.md](offline-bundle-v1.md) + JSON Schema |
| Bundle package implementation boundary | [package-service.md](package-service.md) |
| Background execution/restart/cancel | [operation-manager.md](operation-manager.md) |
| Skopeo | [skopeo-service.md](skopeo-service.md) |
| Helm OCI | [helm-oci-service.md](helm-oci-service.md) |
| Архитектурные решения | [decisions.md](decisions.md) и `docs/adr/` |
| Deployment/runtime | [deploy/README.md](../deploy/README.md) |
| Testing/CI | [testing.md](testing.md) |
| Frontend structure | [frontend.md](frontend.md) |
| Security/trust model | [security.md](security.md) |

## 20. Исторический design document

`docs/harbor-transfer-portal.md` — исходный объединённый документ постановки задачи, UI-концепции, ранних примеров и плана разработки. Он полезен как historical product/design reference, но не является нормативным описанием текущего protocol/runtime поведения.

Если исторический документ противоречит текущему code/tests, принятому ADR, Bundle Protocol или этому architecture document, используется более актуальный специализированный источник, а расхождение считается documentation defect.

## 21. Правило обновления

При изменении архитектурной границы в той же итерации определяется documentation impact:

- protocol/schema → protocol doc + schema + tests + ADR при несовместимом решении;
- service/execution boundary → architecture + specialized component doc;
- secret/trust решение → security + ADR + deployment/admin docs;
- operation lifecycle/restart/cancellation → operation-manager + architecture + testing/user/admin docs по мере появления UX;
- завершённый пользовательский flow → current-state table + user guide;
- release/install → deployment/admin/release docs.

Документация не должна описывать планируемую функцию как уже доступную пользователю.
