# Отложенные задачи (Deferred)

Статус: **На рассмотрении / Отложено** — не в текущем спринте, требующих уточнения требований или ресурсов.

---

## 2. Интеграция Nexus в main — заблокирована региональной приёмкой

**Актуальное состояние (2026-11-06):** интеграция Docker v2 и обычного Helm HTTP находится в [draft PR #451](https://github.com/askarahodov/Harbor-Transfer-Portal/pull/451); это **не** готовый merge в `main` и не production-релиз. Ранее упомянутая ветка `feat/nexus-support` не является текущим release candidate.

**Причина откладывания:** отсутствуют разрешённые региональные SOURCE/TARGET проверки TLS/CA/ACL, физического signed handoff, operator evidence и owner/security approval по [#436](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/436) и [#434](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/434). Отдельный Harbor-инцидент [#422](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/422) требует логов backend для RCA.

**Следующий шаг:** выполнить [региональный runbook](nexus-regional-acceptance-runbook.md) в разрешённой сети, приложить подписанные evidence, затем повторить финальные CI/E2E на одном SHA и получить формальное разрешение на merge/release. До этого статус **NO-GO**.

---

## 3. Файн-тюнинг авторизации

**Причина откладывания:** Текущая реализация sliding session (1 час бездействия) работает. Дополнительные улучшения требуют уточнения threat model и UX требований.

**Кандидаты на будущее:**
- Refresh tokens / rotating tokens (увеличить безопасность при компрометации access token)
- Обновление `last_activity` при **каждом** запросе (сейчас обновляется только при refresh токена)
- WebSocket / SSE уведомления за 5 минут до истечения сессии
- Remember me / long-lived sessions с отдельным refresh token storage

---

## 5. Production hardening

**Причина откладывания:** Требует инфраструктурных решений (Prometheus, Grafana, alerting stack) и согласования SLA/SLO.

**Кандидаты:**
- Rate limiting на все API endpoints (помимо login)
- Audit logging для Nexus операций (кто, когда, что делал)
- Metrics/Prometheus экспорт (latency, error rates, queue sizes)
- Distributed tracing (OpenTelemetry/Jaeger)
- Автоматические бэкапы SQLite + point-in-time recovery
- Chaos engineering / failure injection тесты

---

## Как вернуть в работу

Каждую задачу можно активировать, создав issue в GitHub с label `deferred` и назначив milestone. Приоритет пересматривается на планировании следующего спринта.