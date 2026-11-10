# Отложенная работа и границы релиза

Этот файл **не является отдельным backlog**. Он фиксирует только принцип: актуальные статусы, владельцы, зависимости и приоритеты живут в [GitHub Issues](https://github.com/askarahodov/Harbor-Transfer-Portal/issues), а инженерные gate — в [ROADMAP](../ROADMAP.md). Старые пронумерованные планы и оценки удалены, потому что дублировали и искажали реальные состояния.

## Что ещё не подтверждено

| Тема | Источник | Граница завершения |
| --- | --- | --- |
| Исторический инцидент фонового экспорта | [#422](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/422) | Исправления кода уже в `main`, но traceback и deployed SHA инцидента 03.11.2026 отсутствуют |
| Harbor ↔ Nexus физическая передача | [#434](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/434) | Реальный изолированный SOURCE/TARGET handoff, receipt и operator evidence |
| Nexus региональная приёмка | [#436](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/436) | Реальные TLS/CA, ACL, версии, согласования; отсутствие конкурентных TARGET writers |
| Nexus contract/integration | [#414](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/414) и [Draft #451](https://github.com/askarahodov/Harbor-Transfer-Portal/pull/451) | Disposable E2E зелёный, но региональный **NO-GO** |
| First-run bootstrap | [#481](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/481) | Только после безопасной обязательной смены bootstrap-пароля |
| Упрощение Windows/Linux установки | [#482](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/482) | Зависит от #481; не считать one-command install уже работающим |

## Что НЕ является открытым планом

- Отдельная ветка `feat/nexus-support` как путь к релизу: используется Draft #451, не удалённый старый план.
- «Обновление last_activity на каждом API-запросе»: фоновые запросы **не должны** продлевать сессию. Текущий механизм привязан к действиям пользователя, см. [ADR-004](adr/ADR-004-auth-session.md).
- Старый список ручных задач с номерами без Issue: повторное создание ведёт к дублированию работы.

Для новой работы создайте GitHub Issue с критериями приёмки и ссылками на протокол, безопасность или UI-документацию.
