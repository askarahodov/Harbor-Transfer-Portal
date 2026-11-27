# Отложенная работа и границы релиза

Этот файл **не является отдельным backlog**. Он фиксирует только принцип: актуальные статусы, владельцы, зависимости и приоритеты живут в [GitHub Issues](https://github.com/askarahodov/Harbor-Transfer-Portal/issues), а инженерные gate — в [ROADMAP](../ROADMAP.md). Старые пронумерованные планы и оценки удалены, потому что дублировали и искажали реальные состояния.

## Что ещё не подтверждено

Формы сбора evidence для внешних gate — [шаблоны приёмки](acceptance-evidence-templates.md); они **не** являются доказательством выполнения.

| Тема | Источник | Граница завершения |
| --- | --- | --- |
| Исторический инцидент фонового экспорта | [#422](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/422) | Исправления кода уже в `main`, но traceback и deployed SHA инцидента 03.11.2026 отсутствуют |
| Harbor ↔ Nexus физическая передача | [#434](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/434) | Реальный изолированный SOURCE/TARGET handoff, receipt и operator evidence |
| Nexus региональная приёмка | [#436](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/436) | Реальные TLS/CA, ACL, версии, согласования; отсутствие конкурентных TARGET writers |
| Nexus contract/integration | [#414](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/414) и [PR #503](https://github.com/askarahodov/Harbor-Transfer-Portal/pull/503) | Выпущено в v1.1.0; независимые evidence/sign-offs по #434/#436 |
| First-run bootstrap | [#481](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/481) | Только после безопасной обязательной смены bootstrap-пароля |
| Упрощение Windows/Linux установки | [#482](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/482) | Зависит от #481; не считать one-command install уже работающим |

## Снятые с эксплуатации one-shot workflows (Issue #513)

25 файлов `.github/workflows/prune-*-once.yml` и `.github/workflows/branch-cleanup-once.yml` удалены из репозитория: они были одноразовыми (срабатывали на `pull_request`/`push` по путям **своего же файла**), уже выполнили удаление refs в [#513](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/513), но каждый последующий PR/push, затрагивающий их файлы, перезапускал dry-run и засорял Actions UI и диск runs. Вместе с ними удалены их one-shot скрипты `tools/prune_*_once.py`/`tools/prune_import_wizard_alias.py`, `tools/prune_final_refs.py`, `tools/prune_superseded_refs.py` и два манифеста (`tools/final-ref-prune.tsv`, `tools/superseded-branch-prune.tsv`).

Живая инфраструктура **сохранена** и является единственным поддерживаемым способом аудита и очистки refs:

- `tools/branch_inventory_audit.py` — неразрушающий dry-run аудит (workflow `branch-inventory-audit-tests.yml` + `tools/test_branch_inventory_audit.py`);
- `tools/prune_branch_refs.py` — reusable очистка с manifest `tools/branch-prune-candidates.tsv` (сейчас без собственного workflow: запускается вручную при необходимости);
- `tools/atomic_ref_cleanup.py` — server-enforced lease для безопасного удаления (workflow `atomic-ref-cleanup-tests.yml` + `tools/test_atomic_ref_cleanup.py`).

Регрессионный guard `tools/test_documentation_contracts.py::test_workflows_do_not_self_trigger_on_their_own_file` запрещает повторное появление self-triggering one-shot workflow; allowlist пуст. Последний его элемент — workflow `nexus-legacy-e2e.yml` — удалён: реальный Nexus legacy Docker v2 E2E теперь выполняется scoped-шагами внутри `Integration` job в `.github/workflows/ci.yml` под area `nexus_e2e`.

## Что НЕ является открытым планом

- «Обновление last_activity на каждом API-запросе»: фоновые запросы **не должны** продлевать сессию. Текущий механизм привязан к действиям пользователя, см. [ADR-004](adr/ADR-004-auth-session.md).
- Старый список ручных задач с номерами без Issue: повторное создание ведёт к дублированию работы.

Для новой работы создайте GitHub Issue с критериями приёмки и ссылками на протокол, безопасность или UI-документацию.
