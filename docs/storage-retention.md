# Retention и очистка transfer storage

Документ описывает lifecycle физических payload-файлов Harbor Transfer Portal в persistent volume `/app/data`.

## Зачем нужна retention policy

Transfer bundle может занимать десятки GiB. Docker Compose использует named volume `portal-data`, поэтому restart/recreate контейнеров не удаляет:

- SOURCE publications в `data/outgoing`;
- TARGET intake staging в `data/incoming/staged`;
- временную распаковку import в `data/incoming/verified`.

Без lifecycle policy успешные операции постепенно увеличивали бы размер volume.

## SOURCE

После успешного export сохраняются три файла:

- `DELIVERY-....htp.tar.gz`;
- `DELIVERY-....htp.tar.gz.sha256`;
- `DELIVERY-....htp-handoff.json`.

Они остаются доступными для скачивания в течение `EXPORT_BUNDLE_RETENTION_SECONDS`.
По умолчанию это 604800 секунд — 7 суток.

После истечения retention cleanup удаляет только physical publication. История операции,
delivery id, checksum, размер и audit/history metadata остаются в SQLite.

Если пользователь обращается к уже очищенному completed export, API возвращает
`410 Gone` с code `export_bundle_expired`, а не маскирует retention как внутреннюю ошибку.

Активные и незавершённые export operations retention cleanup не затрагивает. Отдельный
startup recovery по-прежнему удаляет неполные publications аварийно завершённых export.

## TARGET

### Успешный import

После успешного import:

1. immutable receipt и история операции сохраняются;
2. persisted bundle в `data/incoming/staged/<storage_key>` удаляется сразу;
3. extraction workspace `data/incoming/verified/import-<operation_id>` удаляется всегда после worker.

Это исключает хранение одновременно archive и его распакованной копии после нормального завершения.

### Failed / partial import

Bundle terminal import с ошибкой не удаляется сразу, чтобы deterministic retry мог использовать
тот же verified physical payload. Он хранится до `IMPORT_BUNDLE_RETENTION_SECONDS`.
По умолчанию это 604800 секунд — 7 суток.

Если несколько retry operations ссылаются на один `storage_key`, cleanup удалит каталог только
когда **все** связанные operations terminal и retention истёк для самой новой из них.

`READY`, `IMPORTING`, `VERIFYING` и другие non-terminal operations защищены от pruning.

Extraction workspace не нужен для retry: bundle проверяется и распаковывается заново. Поэтому
terminal/orphan `import-<id>` workspace может быть удалён сразу.

## Когда выполняется cleanup

Cleanup запускается:

- один раз при startup после operation recovery;
- затем периодически с интервалом `STORAGE_CLEANUP_INTERVAL_SECONDS`;
- по умолчанию — каждый час.

Ошибки periodic cleanup логируются и не останавливают backend. Следующая итерация повторит попытку.

## Настройки

| Environment variable | Default | Назначение |
|---|---:|---|
| `EXPORT_BUNDLE_RETENTION_SECONDS` | `604800` | срок доступности completed SOURCE publication |
| `IMPORT_BUNDLE_RETENTION_SECONDS` | `604800` | срок хранения terminal TARGET bundle для retry/диагностики |
| `STORAGE_CLEANUP_INTERVAL_SECONDS` | `3600` | период фоновой очистки |

Retention допускается от 1 часа до 365 суток. Cleanup interval — от 60 секунд до 24 часов.

## Safety boundary

Cleanup не выполняет произвольный recursive delete:

- export удаляется только по ожидаемым именам конкретного `delivery_id`;
- import staging удаляется только для server-generated 48-symbol hex `storage_key`;
- удаляемый каталог должен быть direct child настроенного storage root;
- symlink не обходится и не используется для выхода за storage root;
- active operation ownership проверяется по SQLite перед pruning;
- произвольные каталоги в staging cleanup не трогает.

## Что остаётся после cleanup

Удаление payload не удаляет:

- Operation history;
- artifact results;
- import receipt;
- audit events;
- bundle SHA-256/size metadata;
- delivery identity.

Таким образом history/report остаются доступны даже после освобождения диска.
