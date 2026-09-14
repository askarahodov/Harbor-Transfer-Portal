# History и audit API

Этот документ описывает текущую backend-границу истории операций и persisted audit trail Harbor Transfer Portal. History, structured logging и audit events дополняют друг друга, но не заменяют persisted operation state.

## История операций

`GET /api/operations` доступен любому аутентифицированному пользователю и возвращает ограниченную страницу summary-записей без списка artifacts. Детальная карточка операции с artifact outcomes читается через `GET /api/operations/{operation_id}`.

Параметры списка:

- `limit` — от 1 до 100, по умолчанию 50;
- `offset` — неотрицательное смещение;
- `type` — `EXPORT` или `IMPORT`;
- `status` — persisted operation status;
- `actor` — точный нормализованный username;
- `delivery_id` — точный delivery id;
- `search` — подстрока для delivery id, actor, comment или safe error code;
- `created_from`, `created_to` — границы по времени создания операции.

Сортировка детерминирована: сначала новые `created_at`, затем больший `id`. Ответ содержит `items`, `total`, `limit`, `offset`. Summary не подгружает `artifact_results`; artifact details запрашиваются только при открытии конкретной операции.

Frontend read-only History flow описан в [history-ui.md](history-ui.md).

## Audit events

`GET /api/audit/events` доступен только роли `admin`. `operator` и `viewer` получают `403` на backend authorization boundary.

Поддерживаются `limit`, `offset`, `event_type`, `result`, `actor`, `created_from`, `created_to`. Максимальный `limit` — 100, сортировка newest-first.

Persisted `audit_events` содержит:

- UTC timestamp;
- actor user id/username либо `system` для результата background execution;
- стабильный `event_type`;
- `result`;
- bounded safe JSON metadata.

Audit metadata строится по allowlist и не должна содержать password/password hash, JWT, Authorization header, Harbor credential, private signing key, raw stderr/upstream body или полный manifest.

## Текущий event coverage

### Authentication

- `auth.login.succeeded` — успешная аутентификация; metadata содержит только роль;
- `auth.login.failed` — неуспешная попытка или rate-limit rejection; metadata содержит только безопасную причину.

Password, token и client address в persisted login audit не записываются.

### User и Harbor administration

- `user.created`, `user.updated`;
- `harbor.settings.updated`;
- `harbor.credential.rotated`;
- `harbor.ca.updated`, `harbor.ca.removed`.

Для user/settings mutations сохраняются identifiers и имена изменённых полей, но не secret values.

### SOURCE export

- `export.created` — persistent operation создана actor-ом;
- `export.cancel.requested` — owner/admin запросил отмену non-terminal operation;
- `export.completed`, `export.failed`, `export.cancelled` — terminal background outcome от `system` actor.

Terminal metadata содержит operation/delivery identifiers и safe `error_code`, если он существует. `error_message` намеренно не копируется в audit event.

### TARGET import

- `import.intake.created` — создана upload/discovery operation;
- `import.verification.started`, `import.verification.succeeded`;
- `import.started` — actor явно запустил mutation flow;
- `import.overwrite.approved` — actor явно разрешил conflict overwrite;
- `import.cancel.requested` — запрос отмены;
- `import.completed`, `import.failed`, `import.rejected`, `import.cancelled` — terminal background outcome.

Overwrite approval всегда actor-attributed и содержит operation/source delivery identifiers без manifest contents.

## Actor intent и system outcome

Audit trail разделяет два типа фактов:

1. **actor-attributed intent** — login, configuration mutation, operation creation, cancel request, import execute/overwrite approval;
2. **system-attributed outcome** — verifier result и terminal state background operation.

Это не позволяет ошибочно приписать пользователю технический failure, случившийся позднее в background worker, и одновременно сохраняет автора security-sensitive решения.

## Signing/trust keys

В текущем v1 signing private key SOURCE и trusted public keys TARGET управляются через filesystem/deployment boundary, а не через runtime admin API. Поэтому портал не может достоверно сформировать persisted actor audit event для внешней замены этих файлов.

Такая замена должна фиксироваться организационной/deployment процедурой. Если появится managed key administration API/installer flow, его mutation обязана получить отдельные audit events до объявления полного in-product key-change audit coverage.

## Structured logging и correlation

Application logging поддерживает plain/JSON output, `X-Request-ID`, operation correlation и formatter-level secret redaction. Логи полезны для диагностики, но UI и audit/history не парсят текст логов как источник product state.

Compose storage/rotation policy, `PORTAL_LOG_MAX_SIZE`, `PORTAL_LOG_MAX_FILES` и operational semantics описаны в [structured-logging.md](structured-logging.md).

Audit event и operation record остаются persisted data в SQLite; log rotation не является способом управления product history и не должна быть единственным способом доказать пользовательское действие или terminal outcome.

## Retention v1

Audit/history metadata автоматически не удаляется. Удаление package files не означает удаление persisted operation/audit records; history должна оставаться понятной по metadata/receipt даже если payload уже отсутствует.

Настраиваемая автоматическая retention policy в текущем v1 не реализована. До её появления backup/restore и управляемое обслуживание SQLite относятся к административной процедуре.
