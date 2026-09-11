# Карта документации Harbor Transfer Portal

Этот файл объясняет, **какой документ для чего используется** и как отличать нормативный contract от текущего описания, плана или исторического материала.

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
5. runtime/deployment — `deploy/README.md` + Compose/Dockerfiles/`.env.example`;
6. administration — `admin-guide.md` + deployment/security sources;
7. diagnostics — `troubleshooting.md` + affected component/security source;
8. historical design document не переопределяет перечисленные sources.

## Основная карта

| Документ | Роль | Статус |
|---|---|---|
| [project-passport.md](project-passport.md) | простое описание продукта и целевого процесса | актуальный product overview |
| [architecture.md](architecture.md) | components, boundaries, data flows, current implementation state | актуальный |
| [frontend.md](frontend.md) | Vue architecture, role/contour routing, SOURCE export wizard | актуальный |
| [export-orchestration.md](export-orchestration.md) | SOURCE export backend/API/publication/download contract | актуальный component doc |
| [import-orchestration.md](import-orchestration.md) | TARGET intake/verify/conflict/import/receipt contract | актуальный component doc |
| [admin-guide.md](admin-guide.md) | bootstrap, Harbor, keys, backup/restore, limits, эксплуатация | актуальный для current Compose; не final offline installer |
| [troubleshooting.md](troubleshooting.md) | symptom → cause → diagnostic → safe resolution | актуальный; UI-specific cases дополняются вместе с flows |
| [offline-bundle-v1.md](offline-bundle-v1.md) | Bundle v1 signing/checksum/archive contract | **нормативный** |
| `schema/` | machine-readable Bundle schemas | **нормативный** |
| [package-service.md](package-service.md) | Bundle build/verify implementation boundary | актуальный component doc |
| [operation-manager.md](operation-manager.md) | background execution/progress/cancel/restart | актуальный component doc |
| [skopeo-service.md](skopeo-service.md) | container transfer service | актуальный component doc |
| [helm-oci-service.md](helm-oci-service.md) | Helm OCI transfer service | актуальный component doc |
| [harbor-browse-api.md](harbor-browse-api.md) | Harbor browse projection/policies | актуальный component doc |
| [security.md](security.md) | security/trust model и known v1 limitations | актуальный |
| [testing.md](testing.md) | test selection и CI policy | актуальный |
| [decisions.md](decisions.md) | ADR registry | актуальный registry |
| [../deploy/README.md](../deploy/README.md) | development/runtime Compose deployment | актуальный; не final offline installer |
| [harbor-transfer-portal.md](harbor-transfer-portal.md) | исходная постановка/UI concepts/ранний plan | **исторический** |

## Текущая карта transfer flow

### SOURCE

Backend orchestration #17 и UI wizard #18 реализованы.

Основные sources:

- [harbor-browse-api.md](harbor-browse-api.md) — выбор metadata из local Harbor;
- [export-orchestration.md](export-orchestration.md) — authoritative validation, OperationManager, Skopeo/Helm, publication, download;
- [frontend.md](frontend.md) — 4-step wizard, reload/poll/cancel, browser download ticket;
- [offline-bundle-v1.md](offline-bundle-v1.md) — переносимый normative contract.

### TARGET

Backend intake/import orchestration #19 реализован; законченный import wizard/UI ещё в разработке.

Основные sources:

- [import-orchestration.md](import-orchestration.md) — upload/discovery, verify-before-mutation, preview/conflict policy, execute, receipt;
- [offline-bundle-v1.md](offline-bundle-v1.md) — normative bundle verification contract;
- [frontend.md](frontend.md) — current UI status; TARGET route не следует считать готовым wizard только по наличию route.

## Пользовательская и эксплуатационная документация v1

Родительская задача: #27.

Текущая декомпозиция:

- #55 — architecture/separation historical master-document — выполнено;
- #56 — security/trust model — выполнено;
- #57 — deployment/config/credential/key synchronization — выполнено;
- #58 — ADR/docs hygiene и карта документации — выполнено;
- #60 — `admin-guide.md` — выполнено;
- #61 — `troubleshooting.md` — current operational guide создан;
- #67 — automated documentation link gate — выполнено;
- #71 — OperationManager architecture synchronization — выполнено;
- #68 — root README current-state — выполнено;
- #17 — SOURCE backend export — выполнено;
- #18 — SOURCE export wizard — выполнено;
- #19 — TARGET backend import orchestration — выполнено;
- #59 — `user-guide.md` — SOURCE section теперь может описываться по фактическому UI; полный SOURCE→TARGET operator guide ждёт завершения TARGET import UI.

Нельзя дописывать незавершённый TARGET UI как будто он уже существует. Backend API contract и готовый пользовательский workflow — разные уровни готовности.

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
| Auth/security/secret handling | security + deployment/admin + ADR при необходимости |
| Environment/config | `.env.example` + deployment/admin docs |
| Frontend flow | frontend + current-state/user guide |
| Operation/history/report state | architecture + user/admin/troubleshooting docs |
| Stable user-facing error code | troubleshooting + affected guide/component doc |
| CI/test policy | testing + workflow docs |
| Release/install | deployment + admin + release notes/checklist |

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
