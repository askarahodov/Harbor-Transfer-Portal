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

Они остаются доступными для скачивания в течение effective `export_bundle_retention_seconds`.
Bootstrap default — `EXPORT_BUNDLE_RETENTION_SECONDS=604800`, то есть 7 суток. Admin может изменить
значение через **Настройки → Политики переноса → Очистка transfer storage** без restart backend.

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
тот же verified physical payload. Он хранится до effective `import_bundle_retention_seconds`.
Bootstrap default — `IMPORT_BUNDLE_RETENTION_SECONDS=604800`, то есть 7 суток. Persisted admin override
задаётся через UI и имеет приоритет над `.env`.

Если несколько retry operations ссылаются на один `storage_key`, cleanup удалит каталог только
когда **все** связанные operations terminal и retention истёк для самой новой из них.

`READY`, `IMPORTING`, `VERIFYING` и другие non-terminal operations защищены от pruning.

Extraction workspace не нужен для retry: bundle проверяется и распаковывается заново. Поэтому
terminal/orphan `import-<id>` workspace может быть удалён сразу.

## Когда выполняется cleanup

Cleanup запускается:

- один раз при startup после operation recovery;
- затем периодически с effective `storage_cleanup_interval_seconds`;
- bootstrap default `STORAGE_CLEANUP_INTERVAL_SECONDS=3600` — каждый час.

Изменение retention/interval через admin UI применяется к runtime `Settings` без restart. Новое значение
retention используется уже следующей cleanup итерацией; новый interval используется следующими sleep cycles periodic task.

Ошибки periodic cleanup логируются и не останавливают backend. Следующая итерация повторит попытку.

## Настройки

Admin UI использует **Настройки → Политики переноса → Очистка transfer storage**:

| UI поле | API/persistent поле | Bootstrap `.env` default |
|---|---|---:|
| Готовые SOURCE пакеты, суток | `export_bundle_retention_seconds` | `EXPORT_BUNDLE_RETENTION_SECONDS=604800` |
| Failed/partial TARGET пакеты, суток | `import_bundle_retention_seconds` | `IMPORT_BUNDLE_RETENTION_SECONDS=604800` |
| Проверка очистки, минут | `storage_cleanup_interval_seconds` | `STORAGE_CLEANUP_INTERVAL_SECONDS=3600` |

UI показывает human-friendly сутки/минуты, но backend API и SQLite хранят секунды. Если admin override ещё
не сохранён, effective значение берётся из `.env`/Settings. После сохранения SQLite `transfer.*` override
имеет приоритет и переживает restart/backup/restore.

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
