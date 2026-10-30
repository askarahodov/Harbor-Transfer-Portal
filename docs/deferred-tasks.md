# Отложенные задачи (Deferred)

Статус: **На рассмотрении / Отложено** — не в текущем спринте, требующих уточнения требований или ресурсов.

---

## 2. Миграция feature branch в main (PR для ревью)

**Причина откладывания:** Требует согласования с командой, code review, возможно — squash/merge strategy.

**Что готово:** `feat/nexus-support` полностью синхронизирован с `main`, все тесты проходят.

**Следующий шаг:** Создать PR на GitHub, назначить reviewers.

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