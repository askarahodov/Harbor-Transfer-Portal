# ROADMAP — текущий инженерный checkpoint (2026-11-09)

Этот план не заменяет GitHub Issues, [docs/project-passport.md](docs/project-passport.md), [docs/testing.md](docs/testing.md) и региональный runbook в draft PR #451. Для текущего состояния сверяйте Issues/PR/Actions непосредственно на GitHub.

## Текущая продуктовая граница

Базовый Harbor-to-Harbor Offline Bundle v1.0.0 задокументирован как release-qualified; production rollout требует отдельного локального approval. Nexus Docker v2 и обычный Nexus Helm HTTP развиваются в draft PR #451; региональная acceptance всё ещё **NO-GO**.

## Зависимости и приоритеты

| Этап | Задача / источник | Gate завершения | Зависимость |
| --- | --- | --- | --- |
| P1 | [#422](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/422): инцидент экспорта | Получены deployed SHA + sanitized traceback, воспроизведён root cause, подтверждён regression fix | Данные регионального оператора |
| P1 | [#436](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/436): Nexus regional acceptance | Подписанный evidence для реальных версий/ACL/TLS/Helm/Docker/handoff, explicit GO | Утверждённое окно и доступ регионального оператора |
| P1 | [#434](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/434): Harbor ↔ Nexus interoperability | Реальный физический handoff, digest/receipt, fail-closed cases и operator evidence | Региональная инфраструктура |
| P1 | [PR #451](https://github.com/askarahodov/Harbor-Transfer-Portal/pull/451): интеграционный кандидат | Независимый review, same-SHA CI/E2E, отсутствие blockers; отдельно release approval | #436, #434, #422 для production GO |
| P2 | [#414](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/414): контрактные и интеграционные тесты | Отмечено покрытие для необходимых Harbor/Nexus сценариев; проверены gaps | Контракты и интеграционная ветка |
| P2 | AI-ready pilot | Один изолированный Issue → отдельная ветка → PR → required CI → review без несанкционированного merge | AGENTS.md + существующий CI |

## Первая итерация — проверяемые шаги

1. Зафиксировать обзор critical diff PR #451: TARGET permissions, Helm hosted-only, OAuth/ACL/TLS, digest drift, подписанный handoff, отказ без частичного bundle и конкретные тесты.
2. Перепроверить потенциальный риск: `NexusDestinationValidator.validate()` возвращает `DestinationCapability(True, True)` после read-only `/v2/` probe. Это **не доказательство push ACL**; изучить UI/plan, фактическую import authorization и тесты, прежде чем считать это дефектом.
3. Выполнить автоматический CI на текущем integrated SHA и анализировать конкретные failures. Не считать disposable Nexus E2E региональной acceptance.
4. Подготовить пакет операторских вопросов/evidence для #422, #434 и #436. Никаких правок production без разрешения.
5. После согласования технических рисков выбрать небольшую independent Issue для пилота одного coding agent; критерии: воспроизводимость, scoped tests, review и зелёный CI.

## Вне рамок текущей итерации

Автоматический merge, самостоятельный production deploy, массовая архитектурная переработка, параллельные агенты и orchestration по GitHub events до успешного пилота. Эти возможности требуют отдельно настроенного runner/permissions и явных ограничений.

## Checkpoint

- **Completed:** базовая ревизия структуры репозитория, прочитаны действующие CONTRIBUTING/testing/CI и перечень изменённых файлов #451.
- **Validation:** наблюдались успешные GitHub CI и Nexus legacy E2E для `fe313a47b`; сами локальные тесты в рамках создания этого Markdown не выполнялись.
- **Findings:** 98 изменённых файлов #451; требуется отдельная проверка трактовки read-only TARGET probe / write eligibility.
- **Remaining:** complete diff review, региональный evidence, issue #422 root cause, AI pilot.
- **Blockers:** реальные региональные credentials и operator evidence для acceptance; approval на release.
- **Next:** CI данного docs-only PR, independent review #451 и фиксация замечаний в GitHub.
