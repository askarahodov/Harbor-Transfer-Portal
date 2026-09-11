# Карта документации Harbor Transfer Portal

Этот файл объясняет, **какой документ для чего используется** и как отличать нормативный контракт от текущего описания, плана или исторического материала.

Документация проекта ведётся на русском языке; технические identifiers, API fields, environment variables, enum и CLI commands сохраняются в исходном виде.

## Статусы документов

Статус описывает роль документа, а не готовность всего продукта к production.

| Статус | Значение |
|---|---|
| **Нормативный** | Определяет совместимый контракт, который реализация обязана соблюдать. Изменение требует согласованного protocol/schema/ADR решения. |
| **Актуальный** | Описывает текущее состояние реализации/архитектуры/эксплуатации и должен меняться вместе с кодом. |
| **Частично реализовано** | Документ содержит текущую основу и явно отделённые ещё не завершённые части. |
| **Запланировано** | Описывает требуемую будущую работу; нельзя читать как доступную функцию. |
| **Исторический** | Сохраняется как исходная постановка/design reference, но не является источником текущего runtime/protocol поведения. |

## Приоритет источников

Если документы расходятся, не пытайтесь молча объединить противоречащие варианты.

Используйте следующий принцип:

1. для **Bundle Protocol compatibility** — `offline-bundle-v1.md` + JSON Schema + соответствующие protocol tests;
2. для **принятого архитектурного решения** — конкретный ADR;
3. для **текущих архитектурных границ и статуса реализации** — `architecture.md` + текущий code/tests;
4. для **компонента** — специализированный service/API/frontend document + code/tests;
5. для **deployment/runtime** — `deploy/README.md` + `compose.yaml`/Dockerfiles/`.env.example`;
6. для **администрирования установки** — `admin-guide.md` вместе с deployment/security sources;
7. исторический design document не должен переопределять ни один из перечисленных источников.

Обнаруженное противоречие считается documentation defect и должно исправляться в той же или отдельной сфокусированной задаче.

## Основная карта

| Документ | Роль | Статус |
|---|---|---|
| [project-passport.md](project-passport.md) | простое описание продукта и целевого процесса | актуальный product overview |
| [architecture.md](architecture.md) | компоненты, boundaries, data flows, current implementation state | актуальный |
| [admin-guide.md](admin-guide.md) | администрирование установки: bootstrap, Harbor, keys, backup/restore, limits и эксплуатация | актуальный для current Compose; не финальный offline installer |
| [offline-bundle-v1.md](offline-bundle-v1.md) | формат Bundle v1, signing/checksum/archive contract | **нормативный** |
| `schema/` | machine-readable protocol schemas | **нормативный** |
| [package-service.md](package-service.md) | реализационная граница Bundle build/verify | актуальный component doc |
| [operation-manager.md](operation-manager.md) | background execution, progress, cancellation, worker ownership и restart reconciliation | актуальный component doc |
| [skopeo-service.md](skopeo-service.md) | container image transfer service | актуальный component doc |
| [helm-oci-service.md](helm-oci-service.md) | Helm OCI transfer service | актуальный component doc |
| [harbor-browse-api.md](harbor-browse-api.md) | Harbor API projection/policies | актуальный component doc |
| [frontend.md](frontend.md) | frontend stack/routes/structure | актуальный, готовность flows сверять с architecture |
| [security.md](security.md) | security/trust model и known v1 limitations | актуальный |
| [testing.md](testing.md) | test selection и CI policy | актуальный |
| [decisions.md](decisions.md) | ADR registry и состояние решений | актуальный registry |
| [../deploy/README.md](../deploy/README.md) | development/runtime Compose deployment | актуальный; не финальный offline installer |
| [harbor-transfer-portal.md](harbor-transfer-portal.md) | исходная постановка, UI/design concepts, ранний plan | **исторический** |

## Пользовательская и эксплуатационная документация v1

Родительская задача: #27.

Текущая декомпозиция:

- #55 — актуальная architecture и separation от historical master-document — выполнено;
- #56 — полная security/trust model — выполнено;
- #57 — deployment/config/credential/key synchronization — выполнено;
- #58 — ADR/docs hygiene и карта документации — выполнено;
- #59 — `user-guide.md` для operator/viewer — ждёт стабильных #17/#19 UI/API flows;
- #60 — `admin-guide.md` — текущая итерация;
- #61 — `troubleshooting.md` — следующий эксплуатационный документ;
- #67 — automated documentation link gate — выполнено;
- #71 — синхронизация architecture с persistent OperationManager — выполнено;
- #68 — свежий root README поверх актуальных sources — выполнено.

`user-guide.md` не следует заполнять вымышленными завершёнными flow. Разделы, зависящие от ещё не реализованной feature-specific export/import/history/report функциональности, создаются после стабилизации соответствующего поведения либо явно маркируются как незавершённые.

Generic `OperationManager` уже реализован, но это execution foundation, а не доказательство готовности пользовательских export/import flows.

## Автоматическая проверка документации

Локальный gate:

```bash
make docs-check
```

Он запускает stdlib-only checker `tools/check_doc_links.py` и его unit tests.

Checker проверяет repository-relative Markdown links в root Markdown, `docs/**/*.md` и `deploy/**/*.md`:

- target file должен существовать;
- local link не может выходить за repository root;
- links внутри fenced code block не трактуются как реальные документы;
- external `http(s)`, `mailto`, `tel`, `data` links не проверяются по сети;
- pure `#anchor` не требует file lookup.

Проверка намеренно **не является внешним URL crawler**: CI не должен зависеть от доступности интернета или третьих сайтов только для проверки документационного diff.

Path-aware CI включает отдельный `Documentation — local links` job для human documentation/tooling scope. Его результат входит в общий `quality-gate`.

## Правило documentation impact

Для каждого значимого изменения определить, какие документы затронуты:

| Изменение | Минимальный documentation impact |
|---|---|
| Bundle schema/protocol | protocol doc + schema + tests + ADR при несовместимом решении |
| Architecture/service boundary | architecture + specialized doc + ADR при значимом решении |
| Background operation lifecycle/restart/cancel | architecture + operation-manager + testing/user/admin docs по мере появления UX |
| Auth/security/secret handling | security + ADR + deployment/admin docs |
| Environment/configuration | `.env.example` + deployment/admin docs |
| Frontend flow | frontend + user guide/current-state marker |
| Operation states/history/reports | architecture + user/admin/troubleshooting/report docs |
| CI/test policy | testing + workflow documentation |
| Release/install | deployment + admin + release notes/checklist |

## Как документировать незавершённую функцию

Допустимые формулировки:

- `реализовано` — поведение существует и проверяется;
- `реализовано как service primitive` — низкоуровневая возможность есть, но end-to-end flow не готов;
- `scaffold / в разработке` — route/view/structure существует без полного поведения;
- `запланировано` — реализации нет.

Не использовать наличие route, mockup, issue или service class как доказательство завершённого пользовательского сценария.

## Исторические материалы

Исторические документы не удаляются автоматически, если сохраняют полезный product/design context. Но они должны быть явно обозначены в актуальной навигации как historical и не использоваться для копирования commands, protocol examples или security decisions без сверки с current sources.

В частности, `harbor-transfer-portal.md` содержит ранние примеры offline-kit/UI/stack и должен читаться только как исходный design reference.

## Review checklist для документационного PR

Перед merge проверить:

- команды, paths, env names и endpoints существуют;
- SOURCE и TARGET не перепутаны;
- нет реальных secrets/credentials/private keys;
- planned behavior не описано как implemented;
- normative protocol не переопределён prose-примером;
- `make docs-check` зелёный;
- security-sensitive recommendation не ослабляет TLS/signature/path/RBAC controls;
- scope diff не включает несвязанный code/refactoring;
- CI выбрал проверки согласно реальному blast radius.