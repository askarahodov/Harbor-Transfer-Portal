# Архитектура Harbor Transfer Portal

**Статус:** актуальное архитектурное описание текущей разработки v1.

Этот документ описывает действующие boundaries и степень реализации Harbor Transfer Portal. Нормативные контракты здесь не переопределяются: формат переносимого пакета задаёт [Offline Bundle Protocol v1](offline-bundle-v1.md), принятые решения — [ADR](decisions.md), background lifecycle — [OperationManager](operation-manager.md), а feature-specific детали — [SOURCE export orchestration](export-orchestration.md), [TARGET import orchestration](import-orchestration.md) и [frontend](frontend.md).

## 1. Назначение и главный инвариант

Harbor Transfer Portal предназначен для офлайн-передачи container images и Helm OCI charts между двумя физически и сетево изолированными Harbor-контурами.

Главный архитектурный инвариант:

> между SOURCE и TARGET отсутствует прямой сетевой путь, и приложение не должно создавать такой путь неявно.

Поэтому используются две независимые установки:

- `SOURCE` взаимодействует только со своим локальным Harbor и создаёт переносимый bundle;
- `TARGET` взаимодействует только со своим локальным Harbor и проверяет/импортирует полученный bundle;
- SOURCE не хранит credentials TARGET;
- TARGET не хранит credentials SOURCE;
- Harbor-to-Harbor replication через границу изоляции не используется;
- физический перенос archive выполняется по организационной процедуре вне сетевой архитектуры приложения.

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
│                         ├→ controlled intake / verifier    │
│                         ├→ OperationManager                │
│                         │    ├→ Skopeo                     │
│                         │    └→ Helm OCI                   │
│                         ├→ Harbor REST API                 │
│                         └→ SQLite / receipts / state       │
│                                      │                    │
│                                      ▼                    │
│                                  local Harbor             │
└───────────────────────────────────────────────────────────┘
```

На текущем этапе реализованы protocol-critical transfer primitives, persistent OperationManager, feature-specific SOURCE export orchestration, TARGET import orchestration и оба операторских transfer wizard. History/report UX, полное cross-contour acceptance и final offline installation kit развиваются отдельно.

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

### API layer

В текущем коде существуют APIs для:

- authentication/users/RBAC;
- health/readiness;
- Harbor browse и Harbor settings;
- SOURCE export preview/start/bundle/download;
- TARGET import upload/discovery/verified preview/execute/receipt;
- generic operation polling/cancel.

Feature-specific APIs применяют server-side contour/RBAC checks. Frontend route guard — UX boundary, но не security boundary.

### Domain layer

`backend/app/domain/` содержит независимые от UI/HTTP/subprocess contracts:

- Bundle manifest/artifact descriptors;
- protocol validation helpers;
- export/import operation state machine;
- per-artifact status;
- receipt model.

Machine-readable Bundle Protocol schemas находятся в `docs/schema/` и должны оставаться согласованными с typed backend model.

### Service layer

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
| `import_orchestrator.py` | TARGET intake/verify/classify/execute/receipt lifecycle |
| `import_preview_projection.py` | verifier-derived signed metadata/policy projection для UI |

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

Resume середины Skopeo/Helm-команды не поддерживается. Interrupted active execution states после restart переходят в safe terminal failure/cancellation согласно persisted state. Это reconciliation, а не transparent resume.

SOURCE дополнительно выполняет ownership-aware cleanup незавершённой publication. TARGET READY state может переживать restart, поскольку Harbor mutation ещё не началась; execution запускается только после явного решения оператора.

## 6. Transfer и package services

### Harbor REST API

Используется только с local Harbor. SOURCE/TARGET portal instances не вызывают Harbor противоположного contour.

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

### SOURCE guarantees

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
- owner/admin download;
- 4-step `/export` wizard с reload recovery;
- failed/partial publication не предлагается как ready delivery;
- большой archive скачивается native browser path через scoped ticket, а не whole-file Axios blob.

Подробнее: [export-orchestration.md](export-orchestration.md) и [frontend.md](frontend.md).

## 9. TARGET import flow

Реализованный flow:

```text
Physical bundle
  → TARGET Import Wizard
  → raw browser upload OR ready-pair discovery
  → private server-generated staging
  → persisted IMPORT operation
  → BundlePackageService.verify_bundle()
  → signature/schema/checksum/archive validation
  → verifier-derived signed manifest projection
  → TARGET Harbor inspection
  → persisted NEW/SAME/CONFLICT/UNKNOWN/ERROR preview
  → explicit safe policy
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
- signed SOURCE metadata и checksum/schema/signature success публикуются UI только после verifier;
- fail-closed `UNKNOWN/ERROR`;
- default conflict = no overwrite;
- overwrite требует request flag и server policy;
- repeated bundle hash/signature verification перед execute;
- repeated target inspection перед каждым mutation;
- `SAME` → safe skip;
- per-artifact outcomes + immutable receipt;
- partial execution failure не заявляется как atomic rollback.

### TARGET frontend guarantees

`/import` реализует три product stages:

1. intake + verification — keyboard file input/drag&drop для browser upload либо incoming discovery для больших transferred bundles;
2. verified preview — SOURCE manifest metadata, отдельные checksum/schema/signature indicators и каждый TARGET classification;
3. import/result — persistent operation polling, per-artifact status/digests, cancel/reload recovery и immutable receipt presentation.

CONFLICT не скрывается в aggregate count. Default action не overwrite-ит конфликт. Overwrite UI доступен только при server policy и требует отдельного confirmation рядом с точным списком conflicting artifacts. `UNKNOWN/ERROR` всегда блокируют execute.

Operation id сохраняется в `sessionStorage`, поэтому browser reload не теряет active verification/import. Client-side UI не является источником crypto truth и не заявляет rollback при partial failure.

Подробнее: [import-orchestration.md](import-orchestration.md) и [frontend.md](frontend.md).

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

### Import baseline

```text
UPLOADED | DISCOVERED
  → VERIFYING
  → READY
  → IMPORTING
  → VERIFYING_TARGET
  → COMPLETED
```

Оба flows имеют legal terminal `FAILED/CANCELLED`; import также использует `REJECTED` для bundle, не прошедшего verification. Terminal state дальше не переходит.

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

- non-secret Harbor overrides могут храниться в SQLite;
- managed Harbor credential хранится file-backed и не возвращается API;
- environment/file credential остаётся bootstrap fallback;
- custom CA сохраняется server-side после validation;
- SOURCE private signing key не попадает в bundle;
- TARGET хранит только trusted SOURCE public keys;
- runtime secrets не должны попадать в Git, logs, receipts или frontend payload.

Security contract: [security.md](security.md). Deployment/admin procedures: [admin-guide.md](admin-guide.md) и [deploy/README.md](../deploy/README.md).

## 13. Frontend boundary

Frontend — локальный SPA, а не самостоятельный security/protocol engine.

Он отвечает за:

- role/contour-aware navigation;
- orchestration of typed HTTP APIs;
- transparent presentation persisted operation state;
- safe operator choices;
- reload recovery по operation id;
- accessible/responsive controls.

Он не должен:

- выполнять собственную Harbor-to-Harbor передачу;
- доверять client-generated digest вместо backend authoritative validation;
- считать checksum заменой signature;
- считать HTTP success доказательством crypto trust;
- выдавать ETA, rollback или success, которых backend не подтверждает.

## 14. Current implementation matrix

| Область | Статус |
|---|---|
| Auth/RBAC, Harbor settings/browse | реализовано |
| Offline Bundle v1 build/sign/verify | реализовано |
| Skopeo/Helm transfer primitives | реализовано |
| Persistent OperationManager | реализовано |
| SOURCE backend orchestration | реализовано |
| SOURCE export wizard | реализовано |
| TARGET backend orchestration | реализовано |
| TARGET import wizard | реализовано |
| Immutable TARGET receipt | реализовано |
| Full history/audit/report UX | в разработке |
| User guide по фактическим wizard | следующая документационная задача |
| Full SOURCE→physical→TARGET acceptance | ещё не завершено |
| Final offline installation/release kit | ещё не завершено |

Наличие завершённых SOURCE/TARGET wizard означает готовность product flow на уровне application features, но **не** заменяет финальный cross-contour acceptance и release qualification задачи #28.
