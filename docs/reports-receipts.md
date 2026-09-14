# Отчёты операций и import receipts

Этот документ описывает contract задачи #25 / P7.2. Источником истины остаются persisted `operations`, `artifact_results` и immutable import receipt metadata в SQLite. Отчёты не строятся из container logs и не становятся отдельным состоянием операции.

## CSV report

`GET /api/operations/{operation_id}/report.csv`

Доступен любому аутентифицированному пользователю с тем же read-access, что и History/operation detail. Отчёт доступен только после terminal state операции (`COMPLETED`, `FAILED`, `REJECTED`, `CANCELLED`); для незавершённой операции API возвращает `409 operation_report_not_ready`.

CSV отдаётся как UTF-8 attachment с server-generated filename `operation-{id}.csv`. Одна строка соответствует одному persisted artifact result. Если terminal operation завершилась до создания artifact results, CSV всё равно содержит одну operation-level строку с пустыми artifact columns и safe operation error.

Стабильные колонки:

- `operation_id`;
- `delivery_id`;
- `source_delivery_id`;
- `operation_type`;
- `operation_status`;
- `actor`;
- `started_at`;
- `finished_at`;
- `artifact_type`;
- `repository`;
- `name`;
- `reference`;
- `version`;
- `source_digest`;
- `target_digest`;
- `artifact_result`;
- `error_code`;
- `error_message`;
- `operation_comment`;
- `bundle_sha256`.

Для защиты при открытии CSV в spreadsheet приложениях текстовые значения, у которых первый non-whitespace символ равен `=`, `+`, `-` или `@`, получают ведущий апостроф. Это не меняет persisted source data — преобразование применяется только к downloadable CSV representation.

## PDF report

`GET /api/operations/{operation_id}/report.pdf`

Authorization и terminal-state policy совпадают с CSV. PDF содержит:

- product/contour identity;
- operation/delivery/type/status/actor/timestamps;
- bundle SHA256 и operation comment;
- summary counts;
- persisted artifact outcomes, source/target digests и safe errors;
- для import — доступные SOURCE metadata, signing-key fingerprint и persisted checksum/signature/schema verification flags.

PDF строится напрямую из persisted operation data. Для больших документов используется `SpooledTemporaryFile`: малый report остаётся в памяти, после установленного порога temporary storage автоматически переносится на filesystem. Клиент получает streaming response, temporary handle закрывается после отправки.

## Offline fonts

PDF не обращается к CDN или интернет-ресурсам. Backend image устанавливает Debian package `fonts-dejavu-core`; ReportLab использует локальный DejaVu Sans/DejaVu Sans Bold для Unicode и кириллицы. Если service запускается вне штатного container image и DejaVu отсутствует, renderer использует встроенный PDF fallback font, где Unicode coverage может быть ограничена.

## Canonical TARGET receipt

Существующий JSON receipt остаётся отдельным от PDF report:

- machine/API view: `GET /api/imports/{operation_id}/receipt`;
- attachment download: `GET /api/imports/{operation_id}/receipt/download`.

Download сериализует persisted `import_receipt_json` через текущую `ImportReceiptResponse` schema и использует server-generated filename `import-receipt-{id}.json`. Пользователь не передаёт filesystem path или имя файла.

Receipt сохраняет существующую TARGET authorization policy: admin может читать receipt любой import operation; operator — только собственной; viewer не получает этот import-domain endpoint. History CSV/PDF при этом остаются read-only и доступны viewer так же, как operation detail.

## History UI

В drawer деталей terminal operation экран `/history` показывает две read-only команды: **«Скачать CSV»** и **«Скачать PDF»**. Frontend выполняет запрос через общий authenticated API client, поэтому bearer/session contract остаётся тем же, что и у History API.

Для import operation, если текущая роль уже имеет право читать canonical receipt и receipt существует, дополнительно показывается **«Скачать receipt JSON»**. Viewer видит CSV/PDF operation reports, но не получает receipt action; другой operator не получает receipt чужой операции.

Frontend не доверяет имени файла из response header: локальное имя формируется только из числового `operation_id` и фиксированного расширения (`operation-{id}.csv`, `operation-{id}.pdf`, `import-receipt-{id}.json`). Это сохраняет безопасный download UX и не добавляет user-controlled path/filename processing.

## Secret handling

Перед выводом в CSV/PDF текст проходит тот же финальный redaction layer, который используется structured application logging. Password/token/JWT/private-key-like значения не должны намеренно сохраняться в operation data; report redaction является дополнительным защитным слоем, а не разрешением сохранять secrets в `error_message`, comment или artifact metadata.

Downloads используют фиксированные server-generated filenames и `X-Content-Type-Options: nosniff`. Ни один report/receipt endpoint не принимает пользовательский путь.

## Проверки

Regression suite покрывает:

- стабильные CSV columns и реальные persisted artifact outcomes;
- spreadsheet formula injection prefixes;
- terminal failure без artifact rows;
- PDF smoke + persisted operation/source/conflict fields;
- отсутствие intentionally secret-looking values;
- auth/terminal-state/missing-operation behavior;
- owner/admin policy canonical receipt;
- safe `Content-Disposition` filenames;
- History UI CSV/PDF для terminal operation;
- canonical receipt download только для разрешённой роли;
- отсутствие receipt download у viewer при сохранении read-only reports.
