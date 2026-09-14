# History и audit API

Этот документ описывает persisted history/audit contract P6.1. History API, structured logging/correlation и read-only History UI реализованы отдельными срезами; здесь зафиксирован server-side audit coverage и retention behavior.

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

Сортировка детерминирована: сначала новые `created_at`, затем больший `id`. Ответ содержит `items`, `total`, `limit`, `offset`. Summary специально не подгружает `artifact_results`; artifact details запрашиваются только при открытии конкретной операции.

## Audit events

`GET /api/audit/events` доступен только роли `admin`. `operator` и `viewer` получают `403` на уровне backend authorization.

Поддерживаются `limit`, `offset`, `event_type`, `result`, `actor`, `created_from`, `created_to`. Максимальный `limit` — 100, сортировка newest-first.

Каждая запись содержит actor user id/username либо actor `system`, stable event type, result, UTC timestamp и bounded JSON metadata. Repository ограничивает metadata размером 8192 bytes и дополнительно редактирует значения ключей вроде `password`, `token`, `secret`, `credential`, `authorization`, `private_key`.

### Auth

- `auth.login.succeeded` — успешный локальный login, actor — реальный пользователь;
- `auth.login.failed` — неуспешный login, actor — `system`, metadata содержит только безопасную причину (`invalid_credentials`/`throttled`) и при необходимости throttle scope.

Raw attempted username, password, bearer/JWT и client address в failed-login audit не сохраняются.

### User и Harbor administration

User-management mutations пишут `user.created` и `user.updated`. Metadata содержит только target identity и имена изменённых полей; password/hash не сохраняются.

Harbor settings/credential/CA mutations используют события `harbor.settings.updated`, `harbor.credential.rotated`, `harbor.ca.updated`, `harbor.ca.removed` и сохраняют только changed field names/status, никогда credential/CA values.

### SOURCE export

- `export.created` — actor-attributed создание export operation;
- `export.cancel.requested` — запрос отмены пользователем;
- `export.completed`, `export.failed`, `export.cancelled` — system lifecycle outcome.

Lifecycle metadata содержит operation/delivery identifiers, status, safe error code и summary counters. Error message/raw subprocess output в audit не копируется.

### TARGET import

- `import.verified` — bundle успешно прошёл verification и operation перешла в `READY`;
- `import.rejected` — verification отклонила bundle;
- `import.started` — actor-attributed запуск mutation TARGET Harbor;
- `import.conflict_overwrite.approved` — отдельная actor-attributed запись только когда пользователь явно разрешил overwrite существующих конфликтов;
- `import.cancel.requested` — запрос отмены пользователем;
- `import.completed`, `import.failed`, `import.cancelled` — system lifecycle outcome.

Overwrite audit содержит operation/source delivery id и conflict count, но не manifest dump и не credentials.

## System lifecycle actor

Background worker outcome нельзя приписывать пользователю как будто именно пользователь сформировал результат. Поэтому terminal/verification events создаются actor=`system`, а user intent (`created`, `started`, `cancel.requested`, overwrite approval) записывается отдельно с реальным actor id/username.

Operation status hook расположен на ORM persistence boundary, поэтому одинаково покрывает обычное worker completion, failure/cancel и restart reconciliation paths.

## Retention

Audit/history metadata в v1 не удаляется автоматически. Удаление transport package files не удаляет persisted operation/audit records. Настраиваемая retention policy может появиться как отдельная admin policy, но до её явного включения audit trail считается сохраняемым состоянием SQLite и должен входить в backup.

## Logging и product history

Structured application logs содержат request/operation correlation и formatter-level redaction, но не являются источником product history. UI/API читают persisted `operations`, `artifact_results`, `audit_events` и immutable import receipt. Парсинг stdout для определения статуса операции не поддерживается.
