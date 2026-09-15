# Архитектура Harbor Transfer Portal

**Статус:** актуальное архитектурное описание release-qualified baseline **v1.0.0**.

Этот документ описывает действующие boundaries и реализованный end-to-end flow Harbor Transfer Portal. Нормативные контракты здесь не переопределяются: формат переносимого пакета задаёт [Offline Bundle Protocol v1](offline-bundle-v1.md), принятые решения — [ADR](decisions.md), background lifecycle — [OperationManager](operation-manager.md), runtime role contract — [Runtime SOURCE/TARGET mode](runtime-mode.md), а feature-specific детали — [SOURCE export orchestration](export-orchestration.md), [TARGET import orchestration](import-orchestration.md) и [frontend](frontend.md).

## 1. Назначение и главный инвариант

Harbor Transfer Portal предназначен для офлайн-передачи container images и Helm OCI charts между физически и сетево изолированными Harbor-контурами.

Главный архитектурный инвариант:

> между физическими SOURCE и TARGET контурами отсутствует прямой сетевой путь, и приложение не должно создавать такой путь неявно.

Один и тот же software/deployment может работать в runtime role `SOURCE` или `TARGET`, но role относится только к текущему Portal instance и его локальному Harbor. В production air-gap topology физически раздельные контуры обычно имеют отдельные installations Portal:

- в `SOURCE` role Portal взаимодействует только со своим local Harbor и создаёт переносимый bundle;
- в `TARGET` role Portal взаимодействует только со своим local Harbor и проверяет/импортирует полученный bundle;
- переключение runtime role не выбирает другой Harbor и не переносит credentials между контурами;
- SOURCE-контур не хранит credentials TARGET Harbor;
- TARGET-контур не хранит credentials SOURCE Harbor;
- Harbor-to-Harbor replication через границу изоляции не используется;
- физический перенос archive выполняется по организационной процедуре вне сетевой архитектуры приложения.

## 2. Контекст системы

```text
┌────────────────────────── SOURCE ──────────────────────────┐
│ Browser → Nginx/Vue → FastAPI                             │
│                         │                                  │
│                         ├→ local Harbor REST API           │
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
│                         ├→ local Harbor REST API           │
│                         └→ SQLite / receipts / state       │
└───────────────────────────────────────────────────────────┘
```

Baseline v1 включает оба browser transfer wizard, history/audit/reports, signed Bundle v1, persistent runtime SOURCE/TARGET mode, offline installation lifecycle, clean-host qualification и isolated SOURCE → physical bundle → TARGET acceptance.

## 3. Runtime deployment

Application runtime содержит два сервиса:

| Компонент | Ответственность |
|---|---|
| `frontend` | Nginx, собранный Vue SPA, same-origin reverse proxy `/api/` |
| `backend` | FastAPI, SQLite, auth/RBAC, Harbor integration, OperationManager, transfer/package services, Skopeo/Helm |

Backend не публикуется напрямую на host в штатной Compose-топологии. Frontend публикует HTTP endpoint и проксирует API во внутреннюю Compose network.

Обе runtime roles используют одни и те же versioned application images. `PORTAL_CONTOUR=SOURCE|TARGET` используется только как bootstrap default новой базы; после первого startup authoritative mode и monotonic revision сохраняются в SQLite. `operator`/`admin` может переключить role через UI/runtime API без rebuild/restart, если нет блокирующих operations. Harbor configuration при этом всегда относится к одному local Harbor текущей installation.

Полный lifecycle persistent runtime mode: [runtime-mode.md](runtime-mode.md).

Baseline v1 использует один backend instance и in-process `asyncio` OperationManager без Redis/Celery. Это осознанная v1 boundary, а не гарантия horizontal multi-instance execution.

Есть два deployment workflow:

- development/runtime Compose — может собирать images и используется для разработки;
- versioned offline kit — содержит заранее собранные images и устанавливается в закрытом контуре без online build/pull.

Подробности: [deploy/README.md](../deploy/README.md) и [offline install kit](../deploy/offline/README.md).

## 4. Backend layers

```text
backend/app/
├── api/       HTTP endpoints и transport validation
├── auth/      authentication / authorization
├── db/        SQLAlchemy models, repositories, session
├── domain/    protocol/operation/receipt contracts
├── schemas/   API request/response models
├── services/  orchestration и integrations
├── utils/     helpers
├── config.py  runtime settings
└── main.py    FastAPI assembly / lifecycle
```

API покрывает authentication/users/RBAC, health/readiness, local Harbor browse/settings, SOURCE export, TARGET import, history/audit/reports, operation polling/cancel и admin-managed transfer/key settings. Server-side contour/RBAC checks являются security boundary; frontend route guards — только UX boundary.

Основные service boundaries:

| Сервис | Ответственность |
|---|---|
| `harbor_client.py` | Harbor REST API локального contour |
| `harbor_settings.py` | effective Harbor config, managed credential/CA |
| `skopeo_service.py` | container transport через structured argv |
| `helm_oci_service.py` | Helm OCI pull/push и controlled workspace |
| `bundle_package_service.py` | build/verify/publish/extract Offline Bundle v1 |
| `operation_manager.py` | persistent background execution/cancel/restart |
| `export_orchestrator.py` | SOURCE selection → transfer → package lifecycle |
| `import_orchestrator.py` | TARGET intake → verify → classify → execute → receipt |
| report/audit services | history, immutable audit evidence, CSV/PDF/report projections |

Payload transport делегируется Skopeo/Helm; portal не переimplementирует OCI registry copy protocol.

## 5. OperationManager как execution boundary

Длительная работа не привязана к HTTP request lifetime. `OperationManager` сохраняет observable state в SQLite и запускает background workers.

Основные гарантии baseline v1:

- `Operation` коммитится до запуска worker;
- state/progress/artifact updates сохраняются persistently;
- concurrency ограничена настройкой;
- operation получает server-generated private workspace;
- disk reserve проверяется до тяжёлой работы;
- progress структурированный, без парсинга logs;
- expected failures сохраняют stable safe error code/message;
- raw exception/stderr/secrets не становятся operation API payload;
- cancel сохраняется persistently и передаётся в worker/subprocess chain.

Resume середины Skopeo/Helm-команды после restart не поддерживается. Interrupted execution reconciles в безопасное terminal state; TARGET `READY` может пережить restart, потому что registry mutation ещё не началась.

Подробнее: [operation-manager.md](operation-manager.md).

## 6. Offline Bundle v1 как compatibility boundary

SOURCE и TARGET не разделяют runtime state. Их совместимость определяется versioned переносимым contract.

Security-critical members:

```text
manifest.json
manifest.sig
checksums.sha256
images/...
charts/...
```

SOURCE package boundary строит canonical manifest, подписывает его Ed25519 private key, вычисляет payload checksums, создаёт deterministic archive, self-verifies его и публикует readiness `.sha256` последним.

TARGET рассматривает archive как недоверенный input и до mutation выполняет archive/path safety, schema/version, signature/trust и checksum verification.

Несовместимое изменение требует protocol/schema/ADR решения. Нормативные правила: [offline-bundle-v1.md](offline-bundle-v1.md).

## 7. SOURCE export flow

```text
Operator/Admin
  → SOURCE Export Wizard
  → local Harbor browse: project/repository/exact reference
  → authoritative preview
  → persisted export operation
  → repeated Harbor validation
  → Skopeo / Helm export
  → BundlePackageService build/sign/self-verify
  → no-replace publication
  → COMPLETED
  → browser download bundle + .sha256
  → physical transfer
```

Гарантии:

- exact tag/version + digest selection;
- repeated authoritative validation против drift;
- background progress/cancel/restart semantics;
- failed/partial publication не выглядит как ready delivery;
- download разрешён через scoped backend path и disk-backed response;
- bundle metadata содержит runtime `portal_version`.

Подробнее: [export-orchestration.md](export-orchestration.md) и [user-guide.md](user-guide.md).

## 8. TARGET import flow

```text
Physical bundle
  → TARGET Import Wizard
  → browser upload OR ready-pair discovery
  → private staging
  → BundlePackageService.verify_bundle()
  → verifier-derived signed metadata
  → TARGET local Harbor inspection
  → NEW/SAME/CONFLICT/UNKNOWN/ERROR preview
  → explicit policy
  → repeated TOCTOU checks
  → Skopeo / Helm target mutation
  → verification
  → immutable receipt + history/report
```

Гарантии:

- verify-before-mutation;
- `UNKNOWN/ERROR` fail closed;
- `SAME` безопасно пропускается;
- `CONFLICT` не overwrite-ится по умолчанию;
- overwrite возможен только как отдельное authorized действие при разрешённой server policy;
- image target digest проверяется;
- Helm replay учитывает verified SOURCE→TARGET digest provenance, а signed `.tgz` payload проверяется по `payload_sha256`;
- partial execution failure не маскируется как atomic rollback;
- immutable receipt и persisted history сохраняют результаты по артефактам.

Подробнее: [import-orchestration.md](import-orchestration.md), [reports-receipts.md](reports-receipts.md) и [user-guide.md](user-guide.md).

## 9. Persistence и sensitive state

Baseline persistence — SQLite + SQLAlchemy + Alembic. Backend persistent volume монтируется в `/app/data`.

Типовые данные:

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
```

SOURCE private signing key, Harbor credential, JWT/config state и backups являются sensitive. Release archive их не содержит. TARGET хранит только trusted SOURCE public keys, а не SOURCE private key.

Offline lifecycle backup сохраняет `.env` и persistent `/app/data`, поэтому backup считается secret-bearing artifact и должен храниться защищённо.

## 10. Security boundaries

Ключевые v1 controls:

- server-side RBAC `admin/operator/viewer`;
- contour separation;
- local-Harbor-only credentials;
- TLS verification по умолчанию, explicit custom CA;
- no shell-concatenation для Skopeo/Helm пользовательских значений;
- bounded key/archive input;
- safe archive extraction/path validation;
- Ed25519 signature trust отдельно от SHA-256 integrity;
- fail-closed target classification;
- immutable audit/receipt evidence;
- secrets redaction и отсутствие sensitive values в normal API/UI;
- offline release без runtime internet/CDN dependency.

Полная модель угроз и known limitations: [security.md](security.md).

## 11. Offline release v1.0.0

Release identity имеет один canonical product version. Backend/frontend images получают одинаковые OCI `version`/`revision` labels; backend `/api/health`, UI, SOURCE bundle metadata, archive name, `release-version.txt` и `release-manifest.json` должны быть согласованы.

Release build flow:

```text
controlled build environment
  → build-release-images.sh 1.0.0
  → verify OCI version/revision labels
  → build-offline-kit.sh 1.0.0
  → backend.tar + frontend.tar
  → compose.yaml + install/lifecycle scripts
  → docs + release notes + changelog
  → CHECKSUMS.sha256
  → harbor-transfer-portal-v1.0.0-offline-install.tar.gz
  → external .sha256
```

Closed-contour install:

```text
offline archive
  → checksum verification
  → docker load bundled images
  → exact local image/version/architecture checks
  → safe .env creation/preservation
  → docker compose up --no-build --pull never --wait
```

Подробности: [offline install kit](../deploy/offline/README.md) и [offline lifecycle](offline-lifecycle.md).

## 12. Release qualification gates

CI использует path-aware scope и агрегирующий `quality-gate`. В зависимости от blast radius доступны:

- backend Ruff → Mypy → Pytest + coverage floor;
- frontend lint/type/unit/build;
- Bundle Protocol regression;
- targeted security regression;
- real Skopeo/Helm integration через disposable local registry;
- Compose smoke;
- clean-host offline install qualification;
- isolated SOURCE → physical bundle → TARGET acceptance;
- documentation local-link gate.

Clean-host gate удаляет release images перед installation phase и доказывает, что один immutable archive устанавливается как SOURCE и TARGET без pull/build, сохраняет state после rerun/restart и сообщает правильную release version.

Isolated acceptance поднимает отдельные SOURCE/TARGET registries, переносит только bundle/sidecar/public trust material, удаляет SOURCE dependency до TARGET phase и автоматически доказывает image digest, Helm semantics, receipt/history/report, replay skip, conflict default-deny и tamper rejection до mutation.

Подробности: [testing.md](testing.md).

## 13. Browser/operator boundary

Штатному operator не нужны `skopeo`, `helm`, `tar` или `sha256sum` для transfer workflow. SOURCE selection/export/download и TARGET intake/preview/import/history/report доступны через UI. Физическая доставка файлов остаётся организационной процедурой за пределами network architecture Portal.

Пошаговый путь: [user-guide.md](user-guide.md).

## 14. Осознанные ограничения baseline v1

- один backend instance; horizontal multi-instance worker coordination не заявлена;
- resume середины внешней Skopeo/Helm операции после restart не поддерживается;
- rollback уже выполненных независимых TARGET artifact mutations не обещается;
- автоматический DB downgrade при rollback release не обещается — recovery опирается на matching-version backup/restore;
- physical media governance, malware scanning и организационный approval остаются внешними контролями;
- production TLS termination, host hardening, backup retention и Harbor permissions зависят от конкретной площадки.
