# History и audit API

Этот документ описывает backend foundation задачи #87 / P6.1.1. Он не объявляет завершёнными frontend History screen, structured JSON logging или полный audit coverage из родительской задачи #21.

## История операций

`GET /api/operations` доступен любому аутентифицированному пользователю и возвращает ограниченную страницу summary-записей без списка artifacts. Детальная карточка операции с artifact outcomes по-прежнему читается через `GET /api/operations/{operation_id}`.

Параметры списка:

- `limit` — от 1 до 100, по умолчанию 50;
- `offset` — неотрицательное смещение;
- `type` — `EXPORT` или `IMPORT`;
- `status` — persisted operation status;
- `actor` — точный нормализованный username;
- `delivery_id` — точный delivery id;
- `search` — подстрока для delivery id, actor, comment или safe error code;
- `created_from`, `created_to` — границы по времени создания операции.

Сортировка детерминирована: сначала новые `created_at`, затем больший `id`. Ответ содержит `items`, `total`, `limit`, `offset`. Summary специально не подгружает `artifact_results`; это исключает N+1-подобный путь для таблицы истории. Artifact details запрашиваются только при открытии конкретной операции.

## Audit events

`GET /api/audit/events` доступен только роли `admin`. `operator` и `viewer` получают `403` на уровне backend authorization.

Поддерживаются `limit`, `offset`, `event_type`, `result`, `actor`, `created_from`, `created_to`. Максимальный `limit` — 100, сортировка newest-first.

Текущий foundation использует уже существующую таблицу `audit_events`. В ответ попадают:

- actor user id/username;
- event type;
- result;
- безопасная JSON metadata;
- timestamp.

User-management mutations теперь пишут `user.created` и `user.updated`. В metadata сохраняются только `target_user_id`, `target_username` и список имён изменённых полей. Значения password, password hash, JWT и другие secret values в audit event не записываются.

Harbor settings/credential/CA mutations продолжают использовать существующий audit path и также записывают только имена изменённых полей.

## Retention в текущем состоянии

Audit/history metadata не удаляется автоматически. Удаление package files не должно подразумевать удаление persisted operation/audit records. Настраиваемая retention policy относится к последующим задачам и не реализуется этим backend foundation.

## Что остаётся в #21

После P6.1.1 в родительской задаче остаются:

- request correlation id и structured JSON logging;
- audit coverage login/export/import/cancel/failure/overwrite approval;
- frontend History screen с filters/details;
- документированная log rotation/storage policy;
- окончательная retention policy и интеграция report/receipt links.
