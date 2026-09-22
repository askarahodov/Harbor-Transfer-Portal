# Карта документации Harbor Transfer Portal

Этот файл помогает быстро выбрать **правильный источник**: пользовательскую инструкцию, эксплуатационный guide, архитектурный документ, нормативный contract или исторический reference.

Документация проекта ведётся на русском языке; API fields, environment variables, enum, paths и CLI commands сохраняются в исходном виде.

## Локальный Docsify portal

После запуска frontend вся эта Markdown-документация доступна как локальный web-site по
`/docs/` на том же host/port, что и Portal. Например, для стандартной development
конфигурации: `http://127.0.0.1:8080/docs/`.

Docsify и search plugin упакованы во frontend image; runtime не обращается к CDN. Markdown
файлы в репозитории остаются единственным source of truth, а Docsify только отображает их.
Инструкция по запуску, структуре sidebar и добавлению контекстных ссылок из UI:
[docsify.md](docsify.md).

## С чего начать по роли

| Вы работаете как… | Начните здесь |
|---|---|
| operator / viewer | [user-guide.md](user-guide.md) |
| administrator | [admin-guide.md](admin-guide.md) |
| installer / platform engineer | [../deploy/offline/README.md](../deploy/offline/README.md) |
| support / incident responder | [troubleshooting.md](troubleshooting.md) |
| security reviewer | [security.md](security.md) |
| developer / architect | [development.md](development.md), [architecture.md](architecture.md), затем component docs |
| contributor | [development.md](development.md), [../CONTRIBUTING.md](../CONTRIBUTING.md) и [testing.md](testing.md) |
| руководитель / новый участник проекта | [project-passport.md](project-passport.md) |

Root [README](../README.md) — пользовательская входная страница продукта с кратким flow и отдельной developer-секцией.

## Статусы документов

| Статус | Значение |
|---|---|
| **Нормативный** | Определяет совместимый contract; изменение требует protocol/schema/ADR решения. |
| **Актуальный** | Описывает текущую реализацию/архитектуру/эксплуатацию и меняется вместе с кодом. |
| **Частично реализовано** | Содержит текущую основу и явно отделённые незавершённые части. |
| **Запланировано** | Описывает будущую работу и не означает доступную функцию. |
| **Исторический** | Сохраняется как design reference, но не определяет current runtime/protocol behavior. |

Если два current-source документа противоречат друг другу, это documentation defect.

## Приоритет источников

При расхождении используйте следующий порядок:

1. Bundle compatibility — `offline-bundle-v1.md` + JSON Schema + protocol tests;
2. принятое архитектурное решение — соответствующий ADR;
3. current architecture/status — `architecture.md` + code/tests;
4. feature/component behavior — специализированный component/API/frontend doc + code/tests;
5. offline release/install — `../deploy/offline/README.md` + release scripts/qualification tests;
6. development/runtime Compose — `../deploy/README.md` + Compose/Dockerfiles/`.env.example`;
7. administration — `admin-guide.md` + deployment/security sources;
8. user flow — `user-guide.md` + current frontend/backend transfer behavior;
9. diagnostics — `troubleshooting.md` + affected component/security source;
10. historical design documents не переопределяют current sources.

## Пользовательская и эксплуатационная документация

| Документ | Для чего | Статус |
|---|---|---|
| [project-passport.md](project-passport.md) | простое описание продукта, ролей и целевого процесса | актуальный product overview |
| [development.md](development.md) | source build/run на Windows и Linux, Docker Desktop/Engine boundary | актуальный developer workflow |
| [user-guide.md](user-guide.md) | пошаговый browser flow SOURCE → physical transfer → TARGET | актуальный |
| [admin-guide.md](admin-guide.md) | bootstrap, Harbor, users/policies/keys, lifecycle, limits | актуальный |
| [runtime-mode.md](runtime-mode.md) | persistent SOURCE/TARGET role, switch, restart, migration, backup/restore | актуальный runtime/admin contract |
| [key-management.md](key-management.md) | SOURCE signing identity и TARGET trusted-key lifecycle | актуальный |
| [transfer-policies.md](transfer-policies.md) | admin-managed transfer policies и limits | актуальный |
| [troubleshooting.md](troubleshooting.md) | symptom → cause → diagnostic → safe resolution | актуальный |
| [../deploy/offline/README.md](../deploy/offline/README.md) | versioned offline kit install/backup/restore/upgrade/uninstall | актуальный release/install guide |
| [../deploy/README.md](../deploy/README.md) | development/runtime Compose deployment | актуальный development/runtime guide |
| [release-notes-v1.0.0.md](release-notes-v1.0.0.md) | release identity, scope и ограничения v1.0.0 | актуальный release artifact |

## Архитектура, protocol и component docs

| Документ | Роль | Статус |
|---|---|---|
| [architecture.md](architecture.md) | components, boundaries, data flows, current implementation state | актуальный |
| [frontend.md](frontend.md) | Vue architecture, role routing, SOURCE/TARGET wizard | актуальный |
| [export-orchestration.md](export-orchestration.md) | SOURCE export backend/API/publication/download contract | актуальный |
| [import-orchestration.md](import-orchestration.md) | TARGET intake/verify/preview/conflict/import/receipt contract | актуальный |
| [offline-bundle-v1.md](offline-bundle-v1.md) | Bundle v1 signing/checksum/archive contract | **нормативный** |
| `schema/` | machine-readable Bundle schemas | **нормативный** |
| [package-service.md](package-service.md) | Bundle build/verify implementation boundary | актуальный |
| [operation-manager.md](operation-manager.md) | background execution/progress/cancel/restart | актуальный |
| [storage-retention.md](storage-retention.md) | lifecycle и bounded cleanup transfer payload storage | актуальный |
| [skopeo-service.md](skopeo-service.md) | container transfer service | актуальный |
| [helm-oci-service.md](helm-oci-service.md) | Helm OCI transfer service | актуальный |
| [harbor-browse-api.md](harbor-browse-api.md) | Harbor browse projection/policies | актуальный |
| [harbor-profiles.md](harbor-profiles.md) | multi-Harbor profile management и staged operation binding | актуальный backend foundation |
| [history-audit-api.md](history-audit-api.md) | history/audit backend API | актуальный |
| [history-ui.md](history-ui.md) | user-facing history UX | актуальный |
| [reports-receipts.md](reports-receipts.md) | CSV/PDF reports и immutable import receipt | актуальный |
| [security.md](security.md) | security/trust model и known v1 limitations | актуальный |
| [testing.md](testing.md) | scoped test selection, integration/acceptance gates и CI policy | актуальный |
| [decisions.md](decisions.md) | ADR registry | актуальный registry |

Дополнительные специализированные current-source документы:

- [browser-transport.md](browser-transport.md) — browser upload/download transport boundaries;
- [dashboard.md](dashboard.md) — Dashboard behavior;
- [destination-plan-integrity.md](destination-plan-integrity.md) — destination-plan integrity guarantees;
- [deterministic-import-retry.md](deterministic-import-retry.md) — deterministic import retry semantics;
- [offline-lifecycle.md](offline-lifecycle.md) — backup/restore/upgrade/uninstall semantics;
- [structured-logging.md](structured-logging.md) — structured logging contract;
- [universal-mode-key-isolation.md](universal-mode-key-isolation.md) — key/trust isolation for universal runtime mode;
- [admin-user-management.md](admin-user-management.md) — user management details.

## Historical source

[harbor-transfer-portal.md](harbor-transfer-portal.md) — исходная постановка, ранние UI concepts и design plan. Документ сохранён как **исторический reference** и не определяет current runtime, protocol или security behavior.

## Текущий transfer flow

### SOURCE

Основные источники:

- [user-guide.md](user-guide.md) — operator/viewer browser flow;
- [runtime-mode.md](runtime-mode.md) — runtime role и safe switch;
- [harbor-browse-api.md](harbor-browse-api.md) — выбор metadata из local Harbor;
- [export-orchestration.md](export-orchestration.md) — validation, OperationManager, Skopeo/Helm, publication, download;
- [frontend.md](frontend.md) — wizard, reload/poll/cancel, browser download ticket;
- [key-management.md](key-management.md) — SOURCE signing identity;
- [offline-bundle-v1.md](offline-bundle-v1.md) — переносимый normative contract.

### TARGET

Основные источники:

- [user-guide.md](user-guide.md) — intake, verification, preview/conflict, import, receipt/history/report;
- [runtime-mode.md](runtime-mode.md) — runtime role и safe switch;
- [import-orchestration.md](import-orchestration.md) — upload/discovery, verify-before-mutation, preview, execute, receipt;
- [key-management.md](key-management.md) — TARGET trusted keys и overlap rotation;
- [offline-bundle-v1.md](offline-bundle-v1.md) — normative verification contract;
- [frontend.md](frontend.md) — intake/verification, conflict decisions и persistent result flow.

TARGET UI не выполняет самостоятельную cryptographic validation: checksum/schema/signature indicators являются projection успешного backend verifier. `CONFLICT` не overwrite-ится по умолчанию, а `UNKNOWN/ERROR` блокируют mutation.

## Offline release и qualification v1.0.0

Offline release workflow **реализован и квалифицирован**. В контролируемой build/release среде создаются versioned backend/frontend images и immutable installation archive; закрытый контур загружает уже собранные images и запускает их без online build/pull.

Основные sources:

- [../deploy/offline/README.md](../deploy/offline/README.md) — установка и lifecycle;
- [runtime-mode.md](runtime-mode.md) — bootstrap role, switch/restart/migration/backup-restore semantics;
- [offline-lifecycle.md](offline-lifecycle.md) — backup/restore/upgrade/uninstall;
- [release-notes-v1.0.0.md](release-notes-v1.0.0.md) и [../CHANGELOG.md](../CHANGELOG.md) — release identity/changes;
- [testing.md](testing.md) — clean-host и isolated transfer qualification gates.

CI qualification покрывает:

- clean-host installation одного и того же archive в SOURCE/TARGET role;
- сохранение persistent state после rerun/restart/restore;
- отсутствие runtime pull/build зависимости;
- isolated SOURCE → signed bundle → physical boundary → TARGET flow;
- Skopeo/Helm integration, receipt/history/report;
- replay skip, conflict default-deny и tamper rejection до registry mutation;
- согласованность release version между OCI labels, installation metadata и `/api/health`.

Задачи **#27** (documentation set) и **#28** (offline release kit/final acceptance) завершены. Current-source документы не должны описывать их как future work.

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

Path-aware CI включает documentation gate; результат входит в общий `quality-gate`.

## Правило documentation impact

| Изменение | Минимальный impact |
|---|---|
| Bundle schema/protocol | protocol doc + schema + tests + ADR при несовместимости |
| Architecture/service boundary | architecture + specialized doc + ADR при значимом решении |
| Background lifecycle/restart/cancel | architecture + operation-manager + affected UX docs |
| Auth/security/secret handling | security + deployment/admin docs + ADR при необходимости |
| Environment/config | `.env.example` + deployment/admin docs |
| Frontend flow | frontend + user guide/current-state docs |
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
- historical doc не используется как current source;
- `make docs-check` зелёный;
- security recommendation не ослабляет TLS/signature/path/RBAC controls;
- current-state assertions согласованы с code/tests и закрытыми release tasks;
- CI выбрал gates по фактическому blast radius.
