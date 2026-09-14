# Карта документации Harbor Transfer Portal

Этот файл объясняет, **какой документ для чего используется** и как отличать нормативный contract от текущего описания, release-инструкции или исторического материала.

Документация проекта ведётся на русском языке; технические identifiers, API fields, environment variables, enum и CLI commands сохраняются в исходном виде.

## Статусы документов

| Статус | Значение |
|---|---|
| **Нормативный** | Определяет совместимый contract; изменение требует protocol/schema/ADR решения. |
| **Актуальный** | Описывает текущую реализацию/архитектуру/эксплуатацию и меняется вместе с кодом. |
| **Частично реализовано** | Содержит текущую основу и явно отделённые незавершённые части. |
| **Запланировано** | Описывает будущую работу и не означает доступную функцию. |
| **Исторический** | Сохраняется как design reference, но не определяет current runtime/protocol behavior. |

## Приоритет источников

Если документы расходятся, противоречие считается documentation defect. Используйте следующий порядок:

1. Bundle compatibility — `offline-bundle-v1.md` + JSON Schema + protocol tests;
2. принятое архитектурное решение — соответствующий ADR;
3. current architecture/status — `architecture.md` + code/tests;
4. feature/component — специализированный component/API/frontend doc + code/tests;
5. offline release/install — `../deploy/offline/README.md` + release scripts/qualification tests;
6. development/runtime Compose — `../deploy/README.md` + Compose/Dockerfiles/`.env.example`;
7. administration — `admin-guide.md` + deployment/security sources;
8. user flow — `user-guide.md` + current frontend/backend transfer behavior;
9. diagnostics — `troubleshooting.md` + affected component/security source;
10. historical design document не переопределяет перечисленные sources.

## Основная карта

| Документ | Роль | Статус |
|---|---|---|
| [project-passport.md](project-passport.md) | простое описание продукта и целевого процесса | актуальный product overview |
| [architecture.md](architecture.md) | components, boundaries, data flows, current implementation state | актуальный |
| [frontend.md](frontend.md) | Vue architecture, role/contour routing, SOURCE/TARGET wizard | актуальный |
| [user-guide.md](user-guide.md) | пошаговый browser flow SOURCE → physical transfer → TARGET для operator/viewer | актуальный |
| [export-orchestration.md](export-orchestration.md) | SOURCE export backend/API/publication/download contract | актуальный component doc |
| [import-orchestration.md](import-orchestration.md) | TARGET intake/verify/preview/conflict/import/receipt contract | актуальный component doc |
| [admin-guide.md](admin-guide.md) | bootstrap, Harbor, users/policies/keys, offline lifecycle, backup/restore, limits | актуальный admin/runtime guide |
| [transfer-policies.md](transfer-policies.md) | admin-managed runtime transfer policies, limits и restart semantics | актуальный component/admin doc |
| [key-management.md](key-management.md) | SOURCE signing identity и TARGET trusted-key lifecycle/rotation | актуальный component/admin doc |
| [troubleshooting.md](troubleshooting.md) | symptom → cause → diagnostic → safe resolution для current transfer flow | актуальный |
| [offline-bundle-v1.md](offline-bundle-v1.md) | Bundle v1 signing/checksum/archive contract | **нормативный** |
| `schema/` | machine-readable Bundle schemas | **нормативный** |
| [package-service.md](package-service.md) | Bundle build/verify implementation boundary | актуальный component doc |
| [operation-manager.md](operation-manager.md) | background execution/progress/cancel/restart | актуальный component doc |
| [skopeo-service.md](skopeo-service.md) | container transfer service | актуальный component doc |
| [helm-oci-service.md](helm-oci-service.md) | Helm OCI transfer service | актуальный component doc |
| [harbor-browse-api.md](harbor-browse-api.md) | Harbor browse projection/policies | актуальный component doc |
| [history-audit-api.md](history-audit-api.md) / [history-ui.md](history-ui.md) | history/audit backend и user-facing history UX | актуальный |
| [reports-receipts.md](reports-receipts.md) | CSV/PDF reports и immutable import receipt | актуальный |
| [security.md](security.md) | security/trust model и known v1 limitations | актуальный |
| [testing.md](testing.md) | scoped test selection, integration/acceptance gates и CI policy | актуальный |
| [release-notes-v1.0.0.md](release-notes-v1.0.0.md) | release notes v1.0.0 | актуальный release artifact |
| [decisions.md](decisions.md) | ADR registry | актуальный registry |
| [../deploy/README.md](../deploy/README.md) | development/runtime Compose deployment | актуальный development/runtime guide |
| [../deploy/offline/README.md](../deploy/offline/README.md) | versioned offline kit install/backup/restore/upgrade/uninstall | актуальный release/install guide |
| [harbor-transfer-portal.md](harbor-transfer-portal.md) | исходная постановка/UI concepts/ранний plan | **исторический** |

## Текущая карта transfer flow

### SOURCE

Backend orchestration #17 и UI wizard #18 реализованы.

Основные sources:

- [user-guide.md](user-guide.md) — пошаговый operator/viewer browser flow;
- [harbor-browse-api.md](harbor-browse-api.md) — выбор metadata из local Harbor;
- [export-orchestration.md](export-orchestration.md) — authoritative validation, OperationManager, Skopeo/Helm, publication, download;
- [frontend.md](frontend.md) — 4-step wizard, reload/poll/cancel, browser download ticket;
- [key-management.md](key-management.md) — SOURCE signing identity и безопасная rotation;
- [offline-bundle-v1.md](offline-bundle-v1.md) — переносимый normative contract.

### TARGET

Backend intake/import orchestration #19 и UI wizard #20 реализованы.

Основные sources:

- [user-guide.md](user-guide.md) — intake, verification, preview/conflict, import, receipt/history/report простым пользовательским языком;
- [import-orchestration.md](import-orchestration.md) — upload/discovery, verify-before-mutation, signed metadata projection, preview/conflict policy, execute, receipt;
- [key-management.md](key-management.md) — TARGET active/disabled trusted keys и overlap rotation;
- [offline-bundle-v1.md](offline-bundle-v1.md) — normative bundle verification contract;
- [frontend.md](frontend.md) — intake/verification, conflict decisions, persistent import/result flow.

TARGET UI не выполняет самостоятельную cryptographic validation: checksum/schema/signature indicators являются projection успешного backend verifier. `CONFLICT` не overwrite-ится по умолчанию, а `UNKNOWN/ERROR` блокируют mutation.

## Offline release и qualification v1.0.0

Offline release workflow реализован. В контролируемой build/release среде создаются versioned backend/frontend images и immutable installation archive; закрытый контур загружает уже собранные images и запускает их без online build/pull.

Основные sources:

- [../deploy/offline/README.md](../deploy/offline/README.md) — установка и lifecycle;
- [offline-lifecycle.md](offline-lifecycle.md) — backup/restore/upgrade/uninstall semantics;
- [release-notes-v1.0.0.md](release-notes-v1.0.0.md) и [../CHANGELOG.md](../CHANGELOG.md) — release identity/changes;
- [testing.md](testing.md) — clean-host и isolated transfer qualification gates.

CI доказывает:

- clean-host installation одного и того же archive в `SOURCE` и `TARGET`;
- сохранение persistent state после rerun/restart;
- отсутствие runtime pull/build зависимости;
- полный isolated SOURCE → signed bundle → physical boundary → TARGET flow с реальными Skopeo/Helm adapters;
- image digest verification, Helm verification semantics, receipt/history/report;
- replay skip, conflict default-deny и tamper rejection до registry mutation;
- согласованность release version между OCI image labels, installation metadata и `/api/health`.

Родительская задача #28 остаётся tracker release qualification и закрывается только после финального documentation/status audit; сами перечисленные capabilities уже реализованы и не являются future work.

## Пользовательская и эксплуатационная документация v1

P7.4/current-source documentation work завершён. Реализованные application slices и release qualification отражены в текущих источниках:

- #17/#18 — SOURCE backend export + wizard;
- #19/#20 — TARGET backend import + wizard;
- #21/#25 — audit/history, reports/receipts;
- #23 — admin users/policies/signing/trust-key console;
- #26 — scoped CI/quality strategy, coverage/type/dependency/integration gates;
- #27 — user/admin/troubleshooting documentation;
- #144/#147 — offline kit foundation и hardening;
- #150/#151 — backup/upgrade/uninstall и verified restore;
- #153 — clean-host offline install qualification;
- #156 — isolated SOURCE → TARGET acceptance;
- #158 — v1.0.0 release identity, UI/API/bundle visibility, changelog/release notes.

## Автоматическая проверка документации

Локальный gate:

```bash
make docs-check
```

Он запускает repository-relative Markdown link checker и его unit tests. Checker проверяет:

- target file существует;
- local link не выходит за repository root;
- links внутри fenced code block не считаются реальными docs links;
- external `http(s)`, `mailto`, `tel`, `data` links не crawl-ятся;
- pure `#anchor` не требует file lookup.

Path-aware CI включает `Documentation — local links`; результат входит в общий `quality-gate`.

## Правило documentation impact

| Изменение | Минимальный impact |
|---|---|
| Bundle schema/protocol | protocol doc + schema + tests + ADR при несовместимости |
| Architecture/service boundary | architecture + specialized doc + ADR при значимом решении |
| Background lifecycle/restart/cancel | architecture + operation-manager + affected UX docs |
| Auth/security/secret handling | security + deployment/admin docs + ADR при необходимости |
| Environment/config | `.env.example` + deployment/admin docs |
| Frontend flow | frontend + current-state/user guide |
| Operation/history/report state | architecture + user/admin/troubleshooting docs |
| Stable user-facing error code | troubleshooting + affected guide/component doc |
| CI/test policy | testing + workflow docs |
| Release/install | offline deployment + admin + release notes/checklist |

## Как документировать незавершённую функцию

Допустимые формулировки:

- `реализовано` — behavior существует и проверяется;
- `реализовано как service primitive` — low-level capability есть, end-to-end flow ещё нет;
- `scaffold / в разработке` — route/view/structure существует без полного behavior;
- `запланировано` — реализации нет.

Наличие route, mockup, issue или service class не является доказательством завершённого user scenario.

## Review checklist документационного PR

Перед merge проверить:

- commands, paths, env names и endpoints существуют;
- SOURCE/TARGET не перепутаны;
- нет real secrets/credentials/private keys;
- planned behavior не описано как implemented;
- normative protocol не переопределён prose example;
- `make docs-check` зелёный;
- security recommendation не ослабляет TLS/signature/path/RBAC controls;
- current-state таблицы согласованы с code/tests;
- CI выбрал gates по фактическому blast radius.
