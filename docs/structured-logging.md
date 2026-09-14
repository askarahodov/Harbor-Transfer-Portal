# Structured logging и correlation

Этот документ фиксирует backend foundation из #90 / P6.1.2 и последующий hardening correlation/redaction.

## Формат логов

По умолчанию backend пишет plain-text application logs. `LOG_JSON=true` переключает formatter на JSON Lines без внешних сервисов и без runtime-зависимости от интернета.

Поддерживаемые настройки:

- `LOG_LEVEL=CRITICAL|ERROR|WARNING|INFO|DEBUG`;
- `LOG_JSON=true|false`.

Каждая application log entry содержит:

- UTC timestamp;
- severity level;
- component/logger name;
- `request_id`;
- `operation_id`;
- безопасное message поле.

`request_id` и `operation_id` равны `-`, если соответствующего контекста нет.

## Request correlation

Backend принимает необязательный `X-Request-ID` только если он состоит из ASCII букв/цифр и символов `._:-`, длиной не более 64 символов. Невалидное или слишком длинное значение не отражается обратно клиенту: backend создаёт новый случайный идентификатор.

`X-Request-ID` возвращается в каждом HTTP response. При CORS-развёртывании header разрешён и exposed явно.

Request completion log содержит только HTTP method, route path, status code и elapsed milliseconds. Query string, headers и body не логируются этим middleware, поэтому bearer token или пользовательские параметры не должны попадать в access-style application record.

Необработанная ошибка возвращается как безопасный `internal_error` с тем же `X-Request-ID`. Backend пишет traceback для диагностики, но formatter выполняет redaction перед выводом.

## Operation correlation

Application runtime использует `CorrelatedOperationManager`, совместимый subclass основного `OperationManager`. Он устанавливает `operation_id` в `ContextVar` вокруг полного lifecycle worker: ожидание execution slot, вызов worker, обработка ошибки/cancel и cleanup.

Это не зависит от имени `asyncio.Task`. Стандартный `asyncio.to_thread()` копирует текущий context, поэтому helper-thread внутри Skopeo/Helm/Harbor orchestration также получает тот же `operation_id` с самого первого log record.

Для кода вне standard operation worker доступен `operation_log_context(operation_id)`. Context manager всегда восстанавливает предыдущее значение после выхода и не должен использоваться как глобальное mutable состояние.

## Redaction

Formatter выполняет финальную redaction непосредственно перед выводом. Regression coverage включает:

- bearer tokens после `Bearer`;
- bare JWT-like значения;
- обычные и env-style ключи `password`, `passwd`, `token`, `secret`, `jwt`, `authorization`, `private_key`, включая префиксы вроде `HARBOR_PASSWORD` и `JWT_SECRET`;
- quoted JSON/key-value secrets, в том числе значения с пробелами;
- PEM private-key blocks;
- exception tracebacks, проходящие через тот же formatter.

Redaction является дополнительным защитным слоем, а не разрешением логировать секреты. Production code по-прежнему не должен намеренно передавать пароли, Harbor credentials, JWT, private signing key или Authorization headers в logger.

## Docker Compose и ротация

Приложение пишет в стандартный container stream. Ротация, размер и срок хранения container logs относятся к Docker daemon / инфраструктурной политике хоста и не реализуются application-кодом. Для production следует настроить ограниченный log driver/rotation на уровне Docker и интегрировать JSON output с локальной системой сбора логов при необходимости.

## Ограничения v1

Этот слой не является product history: UI и API читают operation/audit данные из SQLite, а не парсят stdout. Audit coverage и History UI развиваются отдельными частями #21.
