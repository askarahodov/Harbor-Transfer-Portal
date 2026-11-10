# Документация Harbor Transfer Portal

Это **карта актуальных источников**, а не ещё одно руководство на сотни страниц. Документация на русском; имена API/полей/команд оставлены в исходном виде. В запущенном Portal доступна офлайн по `/docs/` (Docsify), без runtime CDN.

## Найдите свой сценарий

| Ваша задача | Откройте |
| --- | --- |
| Работа с экспортом/импортом | [User guide](user-guide.md) |
| Пользователи, ключи, политики, резервные копии | [Admin guide](admin-guide.md) |
| Запуск source checkout (Windows/Linux) | [Development](development.md) |
| Установка готового offline kit | [Offline installation](../deploy/offline/README.md) |
| Диагностика ошибок | [Troubleshooting](troubleshooting.md) |
| Оценка безопасности | [Security](security.md) |
| Архитектура и сопровождение | [Architecture](architecture.md), [Testing](testing.md) |
| Текущий backlog и release decision | [ROADMAP](../ROADMAP.md), [GitHub Issues](https://github.com/askarahodov/Harbor-Transfer-Portal/issues) |

Быстрый обзор продукта — [паспорт](project-passport.md). Главная страница репозитория — [README](../README.md). Правила участия — [CONTRIBUTING](../CONTRIBUTING.md).

## Статус возможностей — не путать ветки

- **Поддерживается в `main`:** Harbor SOURCE/TARGET, signed Offline Bundle v1, browser/physical handoff, verify-before-import, роли, сессия по **действиям** пользователя, история и аудит.
- **Реализовано в отдельной тестовой ветке, но не выпущено:** Nexus Docker v2 + classic Helm HTTP ([Draft #451](https://github.com/askarahodov/Harbor-Transfer-Portal/pull/451)); CI/disposable E2E успешны, **региональный NO-GO** до [#434](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/434)/[#436](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/436).
- **В работе:** безопасный первый запуск и однокомандная установка ([#481](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/481), [#482](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/482)). Не используйте `admin/admin` как действующий порядок запуска до их слияния и тестирования.
- **Историческая неопределённость:** исправление ошибки экспорта внедрено, но первопричина инцидента 03.11.2026 не доказана ([#422](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/422)).

## Нормативные источники и порядок доверия

1. **Формат/подпись:** [Offline Bundle v1](offline-bundle-v1.md), `docs/schema/` и protocol tests. Текстовые примеры не переопределяют schema.
2. **Принятые решения:** [ADR registry](decisions.md), [ADR-004](adr/ADR-004-auth-session.md), [ADR-005](adr/ADR-005-harbor-secrets-tls.md), [ADR-008](adr/ADR-008-frontend-ui-kit.md), [ADR-009](adr/ADR-009-oci-layout-payload.md).
3. **Текущее поведение:** code/tests + [Architecture](architecture.md) + специализированные инструкции.
4. **Установка:** [deploy/README](../deploy/README.md) (source Compose), [offline kit](../deploy/offline/README.md) (готовая поставка). Это разные сценарии.
5. **История и прошлые планы:** исходные design notes не считаются рабочей документацией.

## Каталог: операции и protocol

- [User guide](user-guide.md), [Export orchestration](export-orchestration.md), [Import orchestration](import-orchestration.md)
- [Browser transport](browser-transport.md), [Physical handoff](physical-handoff.md), [Destination plan integrity](destination-plan-integrity.md)
- [Package service](package-service.md), [Skopeo service](skopeo-service.md), [Helm OCI service](helm-oci-service.md)
- [Operation manager](operation-manager.md), [Deterministic import retry](deterministic-import-retry.md)
- [Offline Bundle v1](offline-bundle-v1.md), [Reports/receipts](reports-receipts.md)

## Каталог: интерфейс и эксплуатация

- [Dashboard](dashboard.md), [Frontend](frontend.md), [History UI](history-ui.md), [History/Audit API](history-audit-api.md)
- [Settings](settings.md), [Admin user management](admin-user-management.md), [Harbor profiles](harbor-profiles.md), [Harbor browse API](harbor-browse-api.md)
- [Runtime mode](runtime-mode.md), [Universal mode key isolation](universal-mode-key-isolation.md)
- [Key management](key-management.md), [Transfer policies](transfer-policies.md), [Storage retention](storage-retention.md)
- [Offline lifecycle](offline-lifecycle.md), [Structured logging](structured-logging.md), [Troubleshooting](troubleshooting.md)
- [Deployment](../deploy/README.md), [Offline install kit](../deploy/offline/README.md)
- [Testing/CI](testing.md), [Docsify shell](docsify.md), [Release notes 1.0.0](release-notes-v1.0.0.md), [CHANGELOG](../CHANGELOG.md)

## Nexus — отдельно от baseline

- [Сравнение с Harbor](registry-comparison.md) — матрица с честным разделением `main`/Draft.
- [Nexus setup](nexus-setup.md) — подготовка **тестового кандидата**, не production-инструкция.
- [Региональная приёмка](https://github.com/askarahodov/Harbor-Transfer-Portal/blob/integration/nexus-oss-release-candidate-451/docs/nexus-regional-acceptance-runbook.md) — runbook из Draft #451.

## Исторический материал (не source of truth)

[Исходный дизайн](harbor-transfer-portal.md) и [первоначальное Nexus ТЗ](nexus-integration-task.md) сведены к коротким страницам-ссылкам на pinned Git history. Старые скрипты-генераторы, устаревшие протоколы, оценки и ASCII-макеты больше не копируются в текущие руководства. [Deferred work](deferred-tasks.md) теперь только перенаправляет в живые Issues.

## Как проверять изменения документации

```bash
make docs-check
```

Gate содержит два независимых слоя: **Markdown link checker** проверяет локальные ссылки и разрешённые Docsify **site-root Markdown targets**; **documentation contract tests** фиксируют runtime/UX/security и запрещают возврат устаревших описаний. Markdown links не должны выходить за пределы репозитория, code fences имеют явный language tag. Дополнительно **Compose smoke** проверяет реальную раздачу Markdown/assets через `/docs/` и отсутствие Vue SPA fallback для несуществующих путей. CI запускает documentation gate по `tools/ci_scope.py`.

При каждом изменении code/API/config обновите соответствующий guide в том же PR. Проверяйте отличия SOURCE/TARGET, значения `main`/Draft, безопасные error codes, существование команд и отсутствие secret/internal DNS. Результат независимого code review и зелёного CI не подменяет реальную региональную приёмку.
