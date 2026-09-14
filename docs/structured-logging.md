# Structured logging и correlation

Этот документ фиксирует backend foundation из #90 / P6.1.2, последующий hardening correlation/redaction и Compose storage policy текущего v1.

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

## Docker Compose storage policy

Приложение пишет в стандартные stdout/stderr container streams. Базовый `compose.yaml` явно использует Docker logging driver `json-file` и одинаковую bounded rotation policy для `backend` и `frontend`.

Настройки в `.env`:

```text
PORTAL_LOG_MAX_SIZE=10m
PORTAL_LOG_MAX_FILES=5
```

Resolved Compose configuration:

```yaml
logging:
  driver: json-file
  options:
    max-size: "10m"
    max-file: "5"
```

`max-size` ограничивает один log segment, `max-file` — количество retained rotated segments на контейнер. Значения по умолчанию предотвращают неограниченный рост Docker `json-file` logs. При изменении этих параметров учитывайте доступный disk, требования локального SOC/SIEM и необходимое окно диагностики.

После изменения `.env` контейнеры должны быть пересозданы, чтобы logging configuration гарантированно применилась:

```bash
docker compose up -d --force-recreate
```

Проверить итоговую конфигурацию:

```bash
docker compose config
```

Просмотр текущих logs:

```bash
docker compose logs backend
docker compose logs frontend
docker compose logs --since 30m backend
```

Container logs управляются Docker host и не являются файлами application data в SQLite или authoritative содержимым `portal-data` volume.

## Внешний сбор логов

Если организации нужен более долгий retention или централизованный анализ, stdout/stderr следует забирать локальным logging pipeline/SIEM. Такой pipeline должен сохранять air-gap boundary и не требовать runtime internet connectivity.

Application не отправляет logs во внешние сервисы самостоятельно. Формат JSON предназначен для локального ingestion, но выбор collector/storage остаётся deployment responsibility.

## Logs, history и audit — разные источники

Container logs предназначены для диагностики и могут исчезнуть из-за rotation policy. Поэтому UI/API не должны парсить stdout как product state.

Authoritative persisted records:

- operations/history — состояния export/import и artifact outcomes;
- `audit_events` — security/administrative actor intent и system outcome;
- receipts/report metadata — когда соответствующий flow их создаёт.

Удаление rotated log segment не удаляет и не изменяет эти records. Аналогично backup `/app/data` не заменяется backup-ом Docker logs.

## Ограничения v1

SOURCE private signing key и TARGET trusted public keys управляются deployment/filesystem procedure, а не runtime admin API. Портал не может достоверно создать in-product actor audit event для внешней замены этих файлов; такие изменения должны фиксироваться deployment/change-management процедурой.

Если key management станет managed runtime feature, его mutations должны получить persisted audit events до снятия этой границы.
