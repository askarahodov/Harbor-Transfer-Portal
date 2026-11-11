# Отложенная работа и границы релиза

Этот файл **не является отдельным backlog**. Он фиксирует только принцип: актуальные статусы, владельцы, зависимости и приоритеты живут в [GitHub Issues](https://github.com/askarahodov/Harbor-Transfer-Portal/issues), а инженерные gate — в [ROADMAP](../ROADMAP.md). Старые пронумерованные планы и оценки удалены, потому что дублировали и искажали реальные состояния.

## Что ещё не подтверждено

## 2. Интеграция Nexus в main — заблокирована региональной приёмкой

**Актуальное состояние (2026-11-06):** интеграция Docker v2 и обычного Helm HTTP находится в [draft PR #451](https://github.com/askarahodov/Harbor-Transfer-Portal/pull/451); это **не** готовый merge в `main` и не production-релиз. Ранее упомянутая ветка `feat/nexus-support` не является текущим release candidate.

**Причина откладывания:** отсутствуют разрешённые региональные SOURCE/TARGET проверки TLS/CA/ACL, физического signed handoff, operator evidence и owner/security approval по [#436](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/436) и [#434](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/434). Отдельный Harbor-инцидент [#422](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/422) требует логов backend для RCA.

**Следующий шаг:** выполнить [региональный runbook](nexus-regional-acceptance-runbook.md) в разрешённой сети, приложить подписанные evidence, затем повторить финальные CI/E2E на одном SHA и получить формальное разрешение на merge/release. До этого статус **NO-GO**.
