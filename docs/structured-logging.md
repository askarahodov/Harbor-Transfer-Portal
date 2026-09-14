# Structured logging и correlation

Этот документ фиксирует backend foundation из #90 / P6.1.2.

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

## Operation correlation

`OperationManager` уже создаёт `asyncio` tasks с именем `operation-<id>`. Correlation filter распознаёт это безопасное server-generated имя и закрепляет `operation_id` в ContextVar текущей task. Поэтому логи сервисов, выполняемые внутри operation worker, получают operation correlation без передачи идентификатора через каждый вызов.

Для кода вне стандартного worker task доступен `operation_log_context(operation_id)`.

## Redaction

Formatter выполняет финальную redaction непосредственно перед выводом. Regression coverage включает:

- bearer/JWT-like token после `Bearer`;
- значения `password`, `passwd`, `token`, `secret`, `jwt`, `authorization` в `key=value` / `key: value` форме;
- PEM private-key blocks.

Redaction является дополнительным защитным слоем, а не разрешением логировать секреты. Production code по-прежнему не должен намеренно передавать пароли, Harbor credentials, JWT, private signing key или Authorization headers в logger.

## Docker Compose и ротация

Приложение пишет в стандартный container stream. Ротация, размер и срок хранения container logs относятся к Docker daemon / инфраструктурной политике хоста и не реализуются application-кодом. Для production следует настроить ограниченный log driver/rotation на уровне Docker и интегрировать JSON output с локальной системой сбора логов при необходимости.

## Ограничения v1

Этот слой не является product history: UI и API должны читать operation/audit данные из SQLite, а не парсить stdout. Полный security/admin audit coverage и frontend History остаются отдельными частями #21.
