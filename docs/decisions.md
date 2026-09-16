# Реестр архитектурных решений (ADR)

Этот реестр показывает не только номера ADR, но и **реальное состояние решения**. Наличие зарезервированного ID не означает, что отдельный ADR-файл существует или решение принято в полном объёме.

Правила статусов документации и приоритет источников описаны в [docs/README.md](README.md).

## Когда нужен ADR

Отдельный ADR создаётся до или вместе с реализацией, если решение влияет на:

- совместимость protocol/schema;
- security/trust boundaries;
- хранение secrets/data;
- deployment/release model;
- service/module boundaries;
- несколько независимых workstreams;
- backward compatibility или необратимую миграцию.

Локальную реализационную деталь не нужно превращать в ADR без архитектурной причины.

## Реестр

| ID | Решение | Статус | Authoritative source / примечание |
|---|---|---|---|
| ADR-001 | Модель изоляции SOURCE/TARGET и граница доверия | Архитектурный invariant зафиксирован; отдельный ADR-файл отсутствует | [architecture.md](architecture.md), EPIC #1. При необходимости изменения модели нужен отдельный ADR, а не правка исторического design document. |
| ADR-002 | Формат переносимого пакета, manifest, checksums и signature | Нормативно зафиксировано protocol contract; отдельный ADR-файл отсутствует | [offline-bundle-v1.md](offline-bundle-v1.md) + JSON Schema; задача #6 завершена. Несовместимое изменение требует нового protocol/ADR решения. |
| ADR-003 | Представление payload container images и multi-arch | **Заменено ADR-009** | Ранний placeholder superseded принятым [ADR-009](adr/ADR-009-oci-layout-payload.md). |
| ADR-004 | [Аутентификация, авторизация и frontend-сессия](adr/ADR-004-auth-session.md) | **Принято** | `sessionStorage` bearer model, backend RBAC remains authoritative. |
| ADR-005 | [TLS, пользовательский CA и хранение credential локального Harbor](adr/ADR-005-harbor-secrets-tls.md) | **Принято** | Managed file-backed credential/CA + bootstrap fallbacks. |
| ADR-006 | Политика конфликтов импорта и идемпотентности | Архитектурный invariant зафиксирован; отдельный ADR-файл отсутствует | Реализовано: `same digest → skip`, `different digest → conflict`, overwrite default-deny и только explicit authorized policy. Authoritative sources: [import-orchestration.md](import-orchestration.md), [security.md](security.md). |
| ADR-007 | Модель офлайн-установки и упаковки зависимостей | Архитектурный invariant зафиксирован; отдельный ADR-файл отсутствует | Versioned prebuilt offline kit, no-pull/no-build runtime, lifecycle scripts, clean-host qualification и isolated acceptance реализованы. Authoritative sources: [../deploy/offline/README.md](../deploy/offline/README.md), [offline-lifecycle.md](offline-lifecycle.md), [testing.md](testing.md). |
| ADR-008 | [Element Plus + Lucide и runtime-источник идентичности контура](adr/ADR-008-frontend-ui-kit.md) | **Принято** | Frontend foundation использует принятое решение. |
| ADR-009 | [OCI image-layout как представление payload контейнерного образа в Bundle Protocol v1](adr/ADR-009-oci-layout-payload.md) | **Принято** | Реализовано в Skopeo/Bundle boundaries; заменяет ранний ADR-003 placeholder. |

## Локальные системные решения UI

### Theme policy — system light/dark

На 2026-09-16 портал поддерживает **системную светлую и тёмную цветовые схемы** через `prefers-color-scheme`; отдельный пользовательский переключатель темы и собственное хранилище preference не вводятся.

Правила реализации:

- palette primitives (`--color-deep-harbor`, `--color-cloud-white` и другие) остаются неизменяемыми исходными цветами и используются только внутри `tokens.css`;
- views/components используют semantic aliases (`--color-surface`, `--color-text`, `--color-border`, `--color-action`, status aliases и другие), поэтому смена режима не меняет смысл palette tokens;
- dark mode переопределяет только semantic aliases внутри `@media (prefers-color-scheme: dark)`;
- Element Plus получает те же semantic aliases, чтобы custom controls и UI-kit не расходились по теме;
- `tools/check_design_tokens.py` автоматически проверяет WCAG-контраст text/status/control/focus пар **отдельно для light и dark**, запрещает raw/named color hardcodes, прямые palette references и неизвестные color tokens в Vue-файлах;
- `prefers-reduced-motion` поддерживается независимо от цветовой схемы и продолжает глобально сокращать переходы и анимации.

Brand surface (sidebar) подключён через semantic `--color-brand-surface`; это явно отделяет постоянную идентичность продукта от общего surface/background режима.
## Значение статусов

### Принято

Существует отдельный ADR-файл с контекстом, вариантами, решением и последствиями. Изменять принятое сквозное решение незаметно внутри implementation PR нельзя.

### Нормативно зафиксировано protocol contract

Решение уже однозначно определено нормативным protocol/schema документом, но отдельный ADR-файл для зарезервированного ID исторически не создан. Это **не** повод создавать пустой ADR задним числом только ради номера.

Если решение потребуется пересмотреть, новый ADR должен объяснить изменение и совместимость.

### Архитектурный invariant зафиксирован

Граница уже является обязательным свойством текущей архитектуры и продукта, но отдельный ADR отсутствует. Смена такого invariant требует ADR до реализации.

### Запланировано

Финальное архитектурное решение или его implementation boundary ещё не завершены. Текущий baseline/issue не следует выдавать за принятое решение целиком.

### Заменено

Исторический placeholder superseded более поздним принятым ADR. Новый код должен следовать актуальному ADR.

## Шаблон ADR

Каждая новая запись должна содержать:

- **Status** — proposed / accepted / superseded / deprecated;
- **Context** — почему решение необходимо;
- **Constraints** — air-gap, security, compatibility, operability и другие ограничения;
- **Options** — реально рассмотренные варианты;
- **Decision** — выбранный вариант;
- **Reasons** — почему выбран;
- **Consequences** — положительные и отрицательные последствия;
- **Security impact**;
- **Testing/verification impact**;
- **Migration/backward compatibility impact**, если применимо;
- **Supersedes / Superseded by**, если решение заменяет другое.

## Правило изменения решения

Принятое или нормативно закреплённое сквозное решение нельзя менять только потому, что локально удобнее другой вариант.

Порядок:

1. зафиксировать причину изменения;
2. определить affected protocol/data/deployment/security boundaries;
3. оценить backward compatibility и rollback;
4. создать/обновить ADR;
5. обновить нормативную документацию и tests;
6. только затем менять реализацию.

## Связанные источники

- [Карта документации](README.md)
- [Актуальная архитектура](architecture.md)
- [Offline Bundle Protocol v1](offline-bundle-v1.md)
- [Security/trust model](security.md)
- [Testing/CI](testing.md)

Если запись реестра расходится с существующим ADR-файлом, это documentation defect; источник решения — сам ADR до исправления реестра.
