# Отчёты операций и import receipts

Этот документ описывает contract задачи #25 / P7.2 и mapped outcome extension #197. Источником истины остаются persisted `operations`, `artifact_results` и immutable import receipt metadata в SQLite. Отчёты не строятся из container logs, не становятся отдельным состоянием операции и не пересчитывают исторические TARGET destinations из текущих admin defaults.

## CSV report

`GET /api/operations/{operation_id}/report.csv`

Доступен любому аутентифицированному пользователю с тем же read-access, что и History/operation detail. Отчёт доступен только после terminal state операции (`COMPLETED`, `FAILED`, `REJECTED`, `CANCELLED`); для незавершённой операции API возвращает `409 operation_report_not_ready`.

CSV отдаётся как UTF-8 attachment с server-generated filename `operation-{id}.csv`. Одна строка соответствует одному persisted artifact result. Если terminal operation завершилась до создания artifact results, CSV всё равно содержит одну operation-level строку с пустыми artifact columns и safe operation error.

Исходные стабильные колонки сохраняются в прежнем порядке:

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

Для mapped import к ним append-only добавлены persisted outcome fields:

- `source_project`;
- `source_repository`;
- `source_reference`;
- `source_version`;
- `target_project`;
- `target_repository`;
- `target_reference` — фактический full reference, связанный с immutable destination plan;
- `destination_plan_id`;
- `destination_plan_hash`;
- `overwrite_approved` — explicit authorization context конкретного import execution, а не утверждение, что overwrite обязательно произошёл.

`artifact_results` дополнительно сохраняет nullable `target_version`, чтобы Helm version была доступна структурированно через History/API без разбора `target_reference`. Для container image version/tag остаётся частью `target_reference`/source reference и `target_version` может быть `null`.

Новые fields nullable для legacy rows и export operations. Отсутствующее historical TARGET значение остаётся пустым и не восстанавливается из текущей mapping policy.

Для защиты при открытии CSV в spreadsheet приложениях все текстовые значения, включая новые mapping fields, у которых первый non-whitespace символ равен `=`, `+`, `-` или `@`, получают ведущий апостроф. Это не меняет persisted source data — преобразование применяется только к downloadable CSV representation.

## PDF report

`GET /api/operations/{operation_id}/report.pdf`

Authorization и terminal-state policy совпадают с CSV. PDF содержит:

- product/contour identity;
- operation/delivery/type/status/actor/timestamps;
- bundle SHA256 и operation comment;
- summary counts;
- persisted artifact outcomes, source/target digests и safe errors;
- для mapped import — SOURCE identity и actual TARGET reference отдельными колонками плюс destination plan id;
- для import — доступные SOURCE metadata, signing-key fingerprint и persisted checksum/signature/schema verification flags.

Helm identity выводится как `repository/chart:version`; chart name не теряется при отображении SOURCE. Длинный TARGET reference может визуально переноситься внутри PDF cell; перенос является только layout и не изменяет сохранённое значение.

PDF строится напрямую из persisted operation/artifact data. Для больших документов используется `SpooledTemporaryFile`: малый report остаётся в памяти, после установленного порога temporary storage автоматически переносится на filesystem. Клиент получает streaming response, temporary handle закрывается после отправки.

## Immutable mapping source

Destination mapping для исторической операции определяется один раз в import execution boundary и сохраняется вместе с artifact outcome **до mapped TARGET preflight и до любой Harbor mutation**. Snapshot связывается с operation id, bundle SHA-256, SOURCE delivery, destination plan id/hash и source artifact identity; несовпадение блокирует worker fail-closed.

Current default image project, Helm project, source→target map и policy revision **не участвуют** в последующем построении History/CSV/PDF.

Правило:

```text
historical report = persisted execution outcome
historical report != current mapping defaults + old SOURCE reference
```

Это критично после изменения admin defaults: старый report должен продолжать показывать тот TARGET project/repository/reference/version, который был подтверждён immutable destination plan для конкретной операции. Snapshot сохраняется и для последующего `SKIPPED`, `FAILED` или conflict path, поэтому attempted destination не теряется, даже если mutation не состоялась.

## Canonical TARGET receipt

Существующий JSON receipt остаётся отдельным от PDF report:

- machine/API view: `GET /api/imports/{operation_id}/receipt`;
- attachment download: `GET /api/imports/{operation_id}/receipt/download`.

Download сериализует persisted `import_receipt_json` через текущую `ImportReceiptResponse` schema и использует server-generated filename `import-receipt-{id}.json`. Пользователь не передаёт filesystem path или имя файла.

Receipt сохраняет destination plan id/hash и per-artifact TARGET mapping из immutable persisted import plan/outcome. Он не обращается к текущим destination defaults при чтении или download. Existing v1-compatible fields `repository/reference/version`, `target_repository` и `final_reference` остаются backward-compatible; incompatible Bundle v1 protocol change не вводится.

Receipt сохраняет существующую TARGET authorization policy: admin может читать receipt любой import operation; operator — только собственной; viewer не получает этот import-domain endpoint. History CSV/PDF при этом остаются read-only и доступны viewer так же, как operation detail.

## Audit mapping

`import.started`, `import.overwrite.approved` и terminal import events связываются с immutable destination plan. Audit metadata содержит plan id/hash, mapping policy revision, общее количество destinations и bounded sample максимум из 20 source→target entries. Если artifacts больше, выставляется `destinations_truncated=true`; полный manifest в audit не дублируется.

Typed `ImportDestinationPlanResponse` является contract для audit metadata. Legacy revision совместимость обеспечивается schema default `mapping_policy_revision=0`, а не runtime `getattr` fallback в production code.

## Offline fonts

PDF не обращается к CDN или интернет-ресурсам. Backend image устанавливает Debian package `fonts-dejavu-core`; ReportLab использует локальный DejaVu Sans/DejaVu Sans Bold для Unicode и кириллицы. Если service запускается вне штатного container image и DejaVu отсутствует, renderer использует встроенный PDF fallback font, где Unicode coverage может быть ограничена.

## History UI

В drawer деталей terminal operation экран `/history` показывает две read-only команды: **«Скачать CSV»** и **«Скачать PDF»**. Frontend выполняет запрос через общий authenticated API client, поэтому bearer/session contract остаётся тем же, что и у History API.

Для mapped import artifact table показывает SOURCE и TARGET отдельно из backend persisted fields. Frontend не выполняет destination mapping самостоятельно и не читает current Settings для исторической строки.

Для import operation, если текущая роль уже имеет право читать canonical receipt и receipt существует, дополнительно показывается **«Скачать receipt JSON»**. Viewer видит CSV/PDF operation reports, но не получает receipt action; другой operator не получает receipt чужой операции.

Frontend не доверяет имени файла из response header: локальное имя формируется только из числового `operation_id` и фиксированного расширения (`operation-{id}.csv`, `operation-{id}.pdf`, `import-receipt-{id}.json`). Это сохраняет безопасный download UX и не добавляет user-controlled path/filename processing.

## Secret handling

Перед выводом в CSV/PDF текст проходит тот же финальный redaction layer, который используется structured application logging. Password/token/JWT/private-key-like значения не должны намеренно сохраняться в operation data; report redaction является дополнительным защитным слоем, а не разрешением сохранять secrets в `error_message`, comment или artifact metadata.

Downloads используют фиксированные server-generated filenames и `X-Content-Type-Options: nosniff`. Ни один report/receipt endpoint не принимает пользовательский путь.

## Проверки

Regression suite покрывает:

- stable CSV base columns + append-only mapped outcome fields;
- реальные persisted source→target artifact outcomes;
- two operations одного SOURCE bundle с разными plan/destination не смешиваются;
- `SKIPPED`/`FAILED` сохраняют attempted TARGET reference;
- legacy rows без mapping snapshot;
- spreadsheet formula injection prefixes, включая TARGET reference;
- PDF smoke + persisted SOURCE/TARGET mapping и Helm chart identity;
- snapshot commit до входа в основной import worker;
- invalid/stale snapshot fail-closed без partial row mutation;
- bounded start/overwrite/terminal audit mapping;
- owner/admin policy canonical receipt;
- safe `Content-Disposition` filenames;
- History UI mapped SOURCE/TARGET projection;
- canonical receipt download только для разрешённой роли;
- отсутствие receipt download у viewer при сохранении read-only reports.
