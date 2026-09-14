# Архитектура Harbor Transfer Portal

**Статус:** актуальное архитектурное описание текущей разработки v1.

Этот документ описывает действующие архитектурные границы и текущую степень реализации Harbor Transfer Portal. Нормативные контракты здесь не переопределяются: формат переносимого пакета задаёт [Offline Bundle Protocol v1](offline-bundle-v1.md), принятые решения — [ADR](decisions.md), background lifecycle — [OperationManager](operation-manager.md), а feature-specific детали — специализированные component docs.

## 1. Назначение и главное ограничение

Harbor Transfer Portal предназначен для офлайн-передачи container images и Helm OCI charts между двумя физически и сетево изолированными Harbor-контурами.

Главный архитектурный инвариант:

> между SOURCE и TARGET отсутствует прямой сетевой путь, и приложение не должно создавать такой путь неявно.

Поэтому используются две независимые установки:

- `SOURCE` взаимодействует только со своим локальным Harbor и создаёт переносимый bundle;
- `TARGET` взаимодействует только со своим локальным Harbor и проверяет/импортирует полученный bundle;
- SOURCE не хранит credentials TARGET;
- TARGET не хранит credentials SOURCE;
- Harbor-to-Harbor replication через границу изоляции не используется;
- физический перенос файла выполняется по организационной процедуре вне сетевой архитектуры приложения.

## 2. Контекст системы

```text
┌────────────────────────── SOURCE ──────────────────────────┐
│ Browser → Nginx/Vue → FastAPI                             │
│                         │                                  │
│                         ├→ Harbor REST API                 │
│                         ├→ OperationManager                │
│                         │    ├→ Skopeo                     │
│                         │    ├→ Helm OCI                   │
│                         │    └→ BundlePackageService       │
│                         └→ SQLite / persistent state       │
│                                      │                    │
│                                      ▼                    │
│                          signed .htp.tar.gz + .sha256      │
└──────────────────────────────────────┬────────────────────┘
                                       │
                             физический перенос
                                       │
┌────────────────────────── TARGET ────▼────────────────────┐
│ Browser → Nginx/Vue → FastAPI                             │
│                         │                                  │
│                         ├→ controlled intake/verifier      │
│                         ├→ OperationManager                │
│                         │    ├→ Skopeo                     │
│                         │    └→ Helm OCI                   │
│                         ├→ Harbor REST API                 │
│                         └→ SQLite / receipts/state         │
│                                      │                    │
│                                      ▼                    │
│                                  local Harbor             │
└───────────────────────────────────────────────────────────┘
```

На текущем этапе реализованы protocol-critical transfer primitives, persistent OperationManager, feature-specific SOURCE export backend orchestration, feature-specific TARGET import backend orchestration и законченный SOURCE export wizard. TARGET import wizard и history/report UX ещё развиваются.

## 3. Runtime deployment

Текущий Docker Compose runtime содержит два application services.

| Компонент | Ответственность |
|---|---|
| `frontend` | Nginx, собранный Vue SPA, same-origin reverse proxy `/api/` |
| `backend` | FastAPI, SQLite, auth/RBAC, Harbor integration, OperationManager, transfer/package services |

Backend не публикуется напрямую на host в штатной Compose-топологии. Frontend публикует HTTP endpoint и проксирует API во внутреннюю Compose network.

Обе роли используют одни application images. Роль установки задаётся `PORTAL_CONTOUR=SOURCE|TARGET`; Harbor configuration всегда относится только к локальному contour.

Baseline v1 использует один backend instance и in-process `asyncio` OperationManager без Redis/Celery. Это текущая осознанная граница, а не гарантия horizontal multi-instance execution.

Подробности: [deploy/README.md](../deploy/README.md).

## 4. Backend layers

```text
backend/app/
├── api/       HTTP endpoints и transport validation
├── auth/      authentication / authorization
├── db/        SQLAlchemy models, repositories, session
├── domain/    protocol/operation/receipt contracts
├── schemas/   API request/response models
├── services/  orchestration и integrations
├── utils/     технические helpers
├── config.py  runtime settings
└── main.py    FastAPI assembly / lifecycle
```

### 4.1. API layer

В текущем коде существуют APIs для:

- authentication/users/RBAC;
- health/readiness;
- Harbor browse и Harbor settings;
- SOURCE export preview/start/bundle/download;
- TARGET import upload/discovery/preview/execute/receipt;
- generic operation polling/cancel.

Feature-specific APIs применяют server-side contour/RBAC checks. Frontend route guard не является security boundary.

### 4.2. Domain layer

`backend/app/domain/` содержит независимые от UI/HTTP/subprocess domain contracts:

- Bundle manifest/artifact descriptors;
- protocol validation helpers;
- export/import operation state machine;
- per-artifact status;
- receipt model.

Machine-readable Bundle Protocol schemas находятся в `docs/schema/` и остаются согласованными с typed backend model.

### 4.3. Service layer

| Сервис | Ответственность |
|---|---|
| `harbor_client.py` | Harbor REST API локального contour |
| `harbor_settings.py` | effective Harbor config, managed credential/CA |
| `skopeo_service.py` | container transport через structured argv |
| `helm_oci_service.py` | Helm OCI pull/push и safe workspace |
| `bundle_package_service.py` | build/verify/publish/extract Offline Bundle v1 |
| `operation_manager.py` | persistent background execution/cancel/restart |
| `export_orchestrator.py` | SOURCE selection→transfer→package lifecycle |
| `export_publication_guard.py` | ownership/no-replace publication protection |
| `export_recovery.py` | startup reconciliation SOURCE publication boundary |
| TARGET import services | streaming intake, verification, conflict preview, execute, receipt |

Harbor REST API используется как local registry control plane/metadata. Payload transport делегируется Skopeo/Helm; portal не переimplementирует registry copy protocol.

## 5. OperationManager как execution boundary

Длительная работа не привязана к HTTP request lifetime. `OperationManager` сохраняет observable state в SQLite и запускает background workers.

Основные гарантии baseline v1:

- `Operation` коммитится до запуска worker;
- worker получает persistent `worker_token`;
- state/progress/artifact updates используют короткие DB sessions;
- concurrency ограничена `OPERATION_MAX_CONCURRENT`;
- operation получает server-generated private workspace;
- disk reserve можно проверить до тяжёлой работы;
- progress структурированный, без парсинга логов;
- ETA не выдумывается, если backend его не знает;
- expected failures сохраняют stable safe error code/message;
- raw exception/stderr/secrets не становятся operation API payload;
- cancel сохраняется persistently и передаётся в worker/subprocess chain.

Подробности: [operation-manager.md](operation-manager.md).

### Restart/recovery v1

Resume середины Skopeo/Helm-команды не поддерживается.

Interrupted active states:

```text
VALIDATING
RUNNING
PACKAGING
VERIFYING
IMPORTING
VERIFYING_TARGET
```

после restart переходят в safe terminal failure/cancellation согласно persisted state. Это reconciliation, а не transparent resume.

SOURCE дополнительно выполняет ownership-aware cleanup незавершённой publication. TARGET staging/READY state обслуживается import orchestration согласно его собственному lifecycle.

## 6. Transfer services

### Harbor REST API

Используется только с local Harbor. Source/target portal instances не вызывают Harbor противоположного contour.

### Skopeo

Container payload передаётся через Skopeo. Bundle v1 использует OCI image-layout согласно [ADR-009](adr/ADR-009-oci-layout-payload.md). Недоверенные значения передаются structured argv без shell-конкатенации. Digest verification является частью success criteria.

Подробнее: [skopeo-service.md](skopeo-service.md).

### Helm OCI

Helm charts pull/push-ятся через Helm OCI service с controlled workspace и explicit conflict semantics.

Подробнее: [helm-oci-service.md](helm-oci-service.md).

### BundlePackageService

На SOURCE package boundary:

1. принимает подготовленные payload;
2. вычисляет checksums;
3. создаёт canonical `manifest.json`;
4. подписывает manifest Ed25519 private key;
5. создаёт deterministic `.htp.tar.gz`;
6. self-verifies archive;
7. публикует archive с no-replace semantics;
8. readiness `.sha256` публикуется последним.

На TARGET verifier рассматривает bundle как недоверенный input до полного archive/schema/signature/checksum validation.

Нормативные правила: [offline-bundle-v1.md](offline-bundle-v1.md). Реализационная граница: [package-service.md](package-service.md).

## 7. Offline Bundle как compatibility boundary

SOURCE и TARGET не разделяют runtime state. Их совместимость определяется переносимым versioned contract.

Security-critical top-level members:

```text
manifest.json
manifest.sig
checksums.sha256
images/...
charts/...
```

`manifest.sig` подтверждает authenticity canonical manifest; SHA-256 контролирует integrity. Checksum не заменяет подпись.

Несовместимое изменение требует protocol/schema/ADR решения. Architecture prose не может молча менять Bundle Protocol semantics.

## 8. SOURCE export flow

Реализованный flow:

```text
Operator/Admin
  → SOURCE Export Wizard
  → Harbor browse: project/repository/exact reference
  → POST /api/exports/preview
  → authoritative digest/type validation
  → POST /api/exports
  → persisted Operation + artifact rows
  → OperationManager
  → repeated Harbor validation
  → Skopeo / Helm export
  → BundlePackageService build/sign/self-verify
  → ownership-aware no-replace publication
  → persisted filename / size / SHA-256
  → COMPLETED
  → scoped browser download ticket
  → disk-backed FileResponse + .sha256
  → physical transfer
```

### SOURCE backend guarantees

Реализованы:

- SOURCE-only + `operator|admin` preview/start;
- stable `project/repository/reference/digest` selection;
- repeated authoritative Harbor validation;
- digest/type drift detection;
- mixed image + Helm fail-fast delivery;
- background operation lifecycle;
- cancellation barrier для blocking packaging;
- ownership-aware no-replace publication;
- startup cleanup incomplete owned publication;
- persisted bundle metadata;
- owner/admin download.

Component contract: [export-orchestration.md](export-orchestration.md).

### SOURCE frontend guarantees

`/export` реализует 4 steps:

1. browse/search/select exact references;
2. backend preview + comment + exact digest confirmation;
3. operation polling/progress/cancel/reload recovery;
4. ready metadata + native archive download + `.sha256` download/instructions.

Selection хранится независимо от текущей browse page. Operation id сохраняется в `sessionStorage`, поэтому refresh не теряет background operation. Partial/failed bundle не предлагается для переноса.

Большой archive не буферизуется через Axios blob. Frontend получает короткоживущий HttpOnly download ticket, scoped на конкретный operation path, после чего браузер скачивает disk-backed `FileResponse` напрямую.

Подробнее: [frontend.md](frontend.md).

## 9. TARGET import flow

Реализованный backend flow:

```text
Physical bundle
  → streaming upload OR ready-pair discovery
  → private server-generated staging
  → persisted IMPORT operation
  → BundlePackageService.verify_bundle()
  → signature/schema/checksum/archive validation
  → TARGET Harbor inspection
  → persisted NEW/SAME/CONFLICT/UNKNOWN/ERROR preview
  → explicit execute policy
  → repeated bundle + target TOCTOU checks
  → OperationManager
  → Skopeo / Helm target mutation
  → target digest verification
  → immutable receipt
```

### TARGET backend guarantees

- только TARGET и `operator|admin`;
- streaming intake с hard size limit без whole-file RAM buffering;
- discovery только archive + readiness `.sha256` pair;
- verify-before-Harbor-mutation;
- fail-closed `UNKNOWN/ERROR`;
- default conflict = no overwrite;
- overwrite требует request flag и server policy;
- repeated bundle hash/signature verification перед execute;
- repeated target inspection перед каждым mutation;
- `SAME` → safe skip;
- per-artifact outcomes + immutable receipt;
- partial execution failure не заявляется как atomic rollback.

Подробности: [import-orchestration.md](import-orchestration.md).

### TARGET frontend status

Backend #19 реализован, но законченный `/import` wizard/UI ещё не завершён. Наличие route не означает готовый операторский TARGET flow.

## 10. Operation state model

Persisted status — источник истины, а не вывод из logs.

### Export baseline

```text
CREATED
  → VALIDATING
  → RUNNING
  → PACKAGING
  → VERIFYING
  → COMPLETED
```

Active/initial states имеют legal переходы в `FAILED`/`CANCELLED`.

### Import baseline

```text
UPLOADED | DISCOVERED
  → VERIFYING
  → READY
  → IMPORTING
  → VERIFYING_TARGET
  → COMPLETED
```

Import также использует `REJECTED`, `FAILED`, `CANCELLED` согласно domain state machine. Terminal state дальше не переходит.

## 11. Persistence and filesystem state

Baseline persistence — SQLite + SQLAlchemy + Alembic. Compose монтирует persistent `/app/data`.

Типовая структура:

```text
/app/data/
├── harbor-transfer-portal.db
├── packages/
├── incoming/
│   └── staged/
├── outgoing/
├── receipts/
│   └── imports/
├── logs/
├── secrets/
├── keys/
└── tmp/
    └── operations/
```

SQLite хранит operation state/ownership/progress и application metadata. Temporary workspace не заменяет persisted state.

DB, secrets/CA, signing/trust keys, receipts и retained packages имеют разные backup/retention требования.

## 12. Configuration and secrets boundary

Каждый instance имеет один effective local Harbor configuration.

Согласно security/ADR model:

- non-secret Harbor overrides могут храниться в SQLite;
- managed Harbor credential хранится file-backed и не возвращается API;
- environment/file credential остаётся bootstrap fallback;
- custom CA сохраняется server-side после validation;
- TLS verification включена по умолчанию;
- SOURCE private signing key существует только на SOURCE;
- TARGET хранит только trusted SOURCE public keys;
- secrets не должны попадать в bundle, frontend state, operation errors, audit metadata или обычные API responses.

Operation settings включают concurrency/workspace/disk reserve/shutdown timeout. Import settings отдельно задают discovery/staging/receipt roots, upload limits и overwrite policy.

## 13. Trust boundaries

Ключевые границы доверия:

1. **Browser ↔ Portal API** — authentication/RBAC/contour policy.
2. **Portal ↔ local Harbor** — credential/TLS; только Harbor текущего contour.
3. **Backend ↔ Skopeo/Helm** — structured argv/environment/private workspace.
4. **HTTP request ↔ background worker** — request DB session не передаётся worker.
5. **OperationManager ↔ SQLite** — worker ownership/cancel/restart reconciliation.
6. **SOURCE operation ↔ outgoing files** — persisted ownership + no-replace publication.
7. **SOURCE ↔ physical bundle** — signature/integrity/readiness.
8. **Physical bundle ↔ TARGET** — полностью недоверенный input до verifier.
9. **TARGET preview ↔ Harbor mutation** — conflict policy + repeated TOCTOU checks.
10. **Browser download ticket ↔ archive** — short TTL + user/operation/path scope.
11. **Persistent volume ↔ host** — DB/secrets/keys/receipts filesystem boundary.

Полная threat model: [security.md](security.md).

## 14. Frontend architecture

Frontend использует Vue 3 + TypeScript, Vue Router, Pinia, Axios, Element Plus и Lucide.

Runtime contour приходит из local backend `GET /api/health`. Router и navigation contour-aware:

- `/export` доступен transfer roles только на SOURCE;
- `/import` доступен transfer roles только на TARGET;
- `viewer` не получает mutation workflows;
- `admin` получает settings и transfer workflow текущего contour.

SOURCE export wizard использует typed `api/exports.ts` + `stores/exportWizard.ts`. Generic operation state, а не subprocess log text, определяет UI phase/progress/result.

TARGET import route пока остаётся незавершённым UI flow несмотря на готовый backend.

Подробнее: [frontend.md](frontend.md).

## 15. Observability, audit и reports

Health/readiness не должны раскрывать secret configuration.

Generic operation state и feature-specific receipts являются structured data. Logs — диагностический канал, не механизм определения `COMPLETED`.

Полный history/audit/report UX ещё развивается отдельными задачами.

## 16. Offline runtime и release boundary

Controlled build environment может использовать внешние registries/package repositories. Закрытый runtime после доставки готовых images не должен зависеть от internet/CDN.

Working development Compose не равен финальному offline release kit. Release-grade offline installer и isolated SOURCE→physical→TARGET acceptance относятся к #28.

## 17. Текущее состояние реализации

| Область | Статус |
|---|---|
| FastAPI foundation / health | реализовано |
| Authentication / users / RBAC | реализовано |
| Harbor settings / credential / CA | реализовано |
| Harbor REST browse/integration | реализовано |
| Skopeo transfer service | реализовано |
| Helm OCI service | реализовано |
| Offline Bundle v1 typed protocol/schema | реализовано |
| Bundle build/sign/verify/safe extraction | реализовано |
| Persistent OperationManager | реализовано |
| Operation polling/cancel/restart reconciliation | реализовано |
| SOURCE export backend orchestration | реализовано |
| SOURCE ownership/no-replace publication guard | реализовано |
| SOURCE export wizard/UI | реализовано |
| Browser disk-backed bundle download | реализовано |
| TARGET streaming intake/discovery | реализовано |
| TARGET verify/preview/conflict/execute backend | реализовано |
| TARGET receipt | реализовано |
| TARGET import wizard/UI | в разработке |
| Dashboard/history/audit/report UX | в разработке |
| Полный user guide обоих UI flows | ожидает завершения TARGET UI |
| Final offline release kit / isolated E2E | запланировано в #28 |

## 18. Источники истины

| Область | Authoritative source |
|---|---|
| Current architecture/status | этот `architecture.md` + code/tests |
| Bundle Protocol | [offline-bundle-v1.md](offline-bundle-v1.md) + JSON Schema |
| SOURCE export | [export-orchestration.md](export-orchestration.md) |
| TARGET import | [import-orchestration.md](import-orchestration.md) |
| Package boundary | [package-service.md](package-service.md) |
| Background lifecycle | [operation-manager.md](operation-manager.md) |
| Skopeo | [skopeo-service.md](skopeo-service.md) |
| Helm OCI | [helm-oci-service.md](helm-oci-service.md) |
| Frontend | [frontend.md](frontend.md) |
| Security/trust | [security.md](security.md) |
| Deployment/runtime | [deploy/README.md](../deploy/README.md) |
| Testing/CI | [testing.md](testing.md) |
| Decisions | [decisions.md](decisions.md) + `docs/adr/` |

## 19. Исторический design document

`harbor-transfer-portal.md` — исходная постановка/UI concepts/ранний plan. Он полезен как historical product/design reference, но не переопределяет текущий code/tests, protocol, ADR или этот current architecture document.

## 20. Правило обновления

При изменении архитектурной границы documentation impact обновляется в той же итерации:

- protocol/schema → protocol doc + schema + tests + ADR при несовместимом решении;
- service/execution boundary → architecture + component doc;
- secrets/trust → security + ADR + deployment/admin docs;
- operation lifecycle → operation-manager + architecture + UX docs;
- frontend flow → frontend + current-state/user docs;
- stable error code → troubleshooting + affected guide/component doc;
- release/install → deployment/admin/release docs.

Документация не должна объявлять planned route/scaffold готовым пользовательским flow.
