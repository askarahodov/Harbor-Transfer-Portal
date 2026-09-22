# TARGET import orchestration

Статус: **актуальный component document** для backend TARGET import flow и его verified UI projection.

Этот документ описывает реализацию `P5.1/P5.2` и дополняет нормативный [Offline Bundle v1](offline-bundle-v1.md), [OperationManager](operation-manager.md), [Skopeo service](skopeo-service.md), [Helm OCI service](helm-oci-service.md) и [frontend](frontend.md). Он не переопределяет формат bundle или криптографический trust contract.

## Назначение

TARGET import orchestration принимает готовый Offline Bundle v1, проверяет его до любых изменений Harbor, строит persisted preview целевого состояния, применяет явную conflict policy и только после этого запускает background import.

HTTP request не выполняет долгий OCI transfer. Intake и запуск возвращают operation id, а дальнейшее состояние читается через generic `/api/operations/{id}` и import-specific preview/receipt endpoints. TARGET wizard является projection этого persisted backend state и не реализует параллельную client-side модель доверия.

## Контур и роли

Import API работает только при `PORTAL_CONTOUR=TARGET`.

Доступ к `/api/imports/*` имеют `operator` и `admin`. Обычный operator может читать preview/receipt и запускать import только для собственной operation. `admin` может работать с любой import operation. `viewer` не получает import endpoints через RBAC dependency.

SOURCE-инстанс отвечает `import_wrong_contour` и не создаёт staging bundle. Frontend router/sidebar отдельно скрывает normal TARGET workflow на SOURCE, но это только UX boundary; authoritative security остаётся на backend.

## Intake

Поддерживаются два режима.

### Streaming upload

`POST /api/imports/upload` принимает raw request body как поток. Backend:

1. проверяет `Content-Length`, если он присутствует;
2. проверяет доступное место с учётом operation disk reserve;
3. создаёт server-generated hex storage key;
4. пишет байты в private `*.part` file без загрузки bundle целиком в RAM;
5. одновременно считает SHA256 и применяет hard byte limit;
6. выполняет `fsync` и `os.replace()` в финальный staging filename;
7. создаёт `IMPORT/UPLOADED` operation и запускает verification worker.

При превышении лимита или ошибке intake временный staging directory удаляется.

Browser UI отправляет `File` как raw request body и показывает browser upload progress, но не обещает этот path для arbitrarily large файлов. При size/disk rejection оператор получает инструкцию использовать incoming/transfer-media flow.

### Incoming discovery

`POST /api/imports/discover` сканирует только файлы `*.htp.tar.gz` непосредственно в `IMPORT_DISCOVERY_ROOT`.

Bundle считается готовым к claim только когда рядом существует обычный файл `<bundle>.sha256`. Архив без readiness sidecar игнорируется, поэтому копируемый или ещё не финализированный файл не попадает в verification.

Готовая пара archive + sidecar атомарно перемещается в server-generated staging directory, после чего создаётся `IMPORT/DISCOVERED` operation. UI получает список созданных operation ids, читает persisted filename/size через generic operation API и позволяет выбрать нужную operation, если найдено несколько bundles.

## Verification до Harbor mutation

Первый background worker переводит operation в `VERIFYING` и вызывает единственный `BundlePackageService.verify_bundle()` path.

Проверяются:

- archive limits и безопасные tar members;
- whole-file SHA256 sidecar для incoming mode;
- canonical `manifest.json` и JSON Schema;
- Ed25519 signature по configured trusted public keys;
- payload checksums и descriptor metadata;
- поддерживаемая major version Bundle Protocol.

Если verification не проходит, operation становится `REJECTED`, staging удаляется, а Skopeo/Helm target inspection не запускается. Таким образом невалидный bundle не меняет Harbor и даже не участвует в conflict preview.

SHA256, вычисленный во время upload/claim, должен совпасть с результатом verifier. Это первая защита от изменения файла после intake.

### Verified preview projection для UI

Frontend не имеет собственного crypto verifier и не выводит успех из HTTP 2xx. После успешного `verify_bundle()` backend сохраняет расширенный `ImportPreviewResponse` со следующими verifier-derived полями:

- `bundle_filename`, `bundle_size_bytes`, `bundle_sha256`, `intake_mode`;
- `source_delivery_id`;
- `source_harbor`, `source_portal_version`;
- `source_created_at`, `source_created_by`, `source_comment` из signed manifest;
- `signing_key_fingerprint`, `verified_at`;
- `checksum_verified=true`;
- `schema_verified=true`;
- `signature_verified=true`;
- `overwrite_allowed`, отражающий текущую server-side `IMPORT_ALLOW_OVERWRITE` policy.

Флаги `*_verified` выставляются только после полного verifier path. Если verification падает, preview не публикуется и operation переходит в `REJECTED`.

Новые projection fields имеют безопасные defaults. Поэтому persisted READY preview, созданный более ранней v1 версией, остаётся читаемым после обновления, но UI не должен заявлять успешную отдельную проверку, если legacy preview не содержит её подтверждения.

## Persisted preview TARGET state

После package verification для каждого manifest artifact TARGET Harbor инспектируется без mutation.

Классификации:

| Состояние | Значение |
|---|---|
| `NEW` | соответствующего target reference/version нет; можно импортировать |
| `SAME` | target существует и его identity подтверждена для ожидаемого artifact; повторный import должен быть skip |
| `CONFLICT` | target reference/version существует с другим подтверждённым содержимым или digest provenance |
| `UNKNOWN` | target существует, но available metadata не позволяет безопасно доказать equality |
| `ERROR` | target inspection не удалось безопасно выполнить |

Для container image authoritative equality по-прежнему строится на OCI digest из manifest.

Для Helm `source_digest` — SOURCE OCI registry provenance, а не переносимый byte identity: повторный `helm push` того же подписанного `.tgz` в независимый registry может получить другой OCI manifest digest. Переносимая identity Helm payload внутри Offline Bundle v1 — подписанный `payload_sha256` самого `.tgz`. После успешного TARGET push портал сохраняет фактически наблюдаемый TARGET OCI digest вместе с SOURCE digest. На replay `SAME` допустим только когда текущий TARGET digest совпадает с ранее сохранённой `VERIFIED` SOURCE→TARGET парой; внешняя замена TARGET digest снова классифицируется как `CONFLICT`.

Preview сохраняется в DB вместе с exact bundle SHA256, source delivery id, размером, signing key fingerprint, signed SOURCE metadata и policy projection. Artifact rows создаются в manifest order и затем используются generic operation status API.

`UNKNOWN` и `ERROR` блокируют execute. Это fail-closed поведение: отсутствие достоверного ответа не трактуется как `NEW`.

## Conflict policy

Default policy:

- `NEW` → import;
- `SAME` → `SKIPPED`, без повторной записи Harbor;
- `CONFLICT` → execute запрещён;
- `UNKNOWN/ERROR` → execute запрещён.

Overwrite требует одновременно:

1. явного `overwrite_conflicts=true` в execute request;
2. server-side `IMPORT_ALLOW_OVERWRITE=true`.

TARGET wizard дополнительно не показывает overwrite как normal action, пока backend preview не сообщает `overwrite_allowed=true`. При наличии conflicts оператор видит **точный список conflicting artifacts и digests** и должен отдельно подтвердить overwrite. Это UX barrier, а не замена backend policy.

Для Helm import используется отдельный policy-aware adapter. Generic Helm OCI service сохраняет safe default и по-прежнему запрещает push поверх существующего chart/version без явного разрешения import engine.

## Защита от TOCTOU

До первого Harbor mutation execution worker повторно:

1. считает SHA256 staging archive и сверяет его с persisted preview;
2. полностью выполняет `verify_bundle()` ещё раз;
3. извлекает payload только из заново проверенного archive;
4. сверяет source delivery id;
5. непосредственно перед каждым artifact import снова инспектирует target state.

Если bundle изменился после preview, operation завершается `FAILED/import_bundle_changed` до Skopeo/Helm mutation.

Повторная target inspection защищает conflict policy от изменений Harbor между preview и execute. Если `NEW` превратился в `CONFLICT`, default policy блокирует именно этот artifact вместо молчаливого overwrite.

## Import execution

После явного execute operation проходит:

`READY → IMPORTING → VERIFYING_TARGET → COMPLETED | FAILED`.

Container images импортируются через Skopeo с `expected_digest` из manifest. Skopeo проверяет локальный OCI payload и повторно инспектирует TARGET digest после copy.

Helm charts валидируются и push-ятся через Helm OCI adapter. Перед фиксацией `VERIFIED` SHA-256 фактически push-нутого локального `.tgz` обязан совпасть с подписанным `payload_sha256` descriptor. Фактический TARGET OCI manifest digest читается после push и сохраняется как TARGET provenance; он не обязан равняться SOURCE OCI manifest digest.

Независимые artifacts обрабатываются последовательно и имеют отдельные outcomes. Ошибка одного artifact не приводит к попытке отката уже успешно импортированного другого artifact. Portal **не заявляет atomic rollback across Harbor artifacts**.

Operation становится `COMPLETED` только если policy успешно выполнена для всех artifacts. При частичном сбое итоговая operation — `FAILED`, а уже выполненные per-artifact результаты сохраняются.

TARGET wizard показывает persisted phase/counters и per-artifact results из generic operation API. Browser reload восстанавливает текущую import operation по сохранённому `operation_id`, а не начинает import заново.

## Idempotency и replay

Повторная доставка того же bundle может создать новую import operation: `source_delivery_id` намеренно не unique. Bundle identity фиксируется через SHA256.

Для image target с ожидаемым OCI digest новый preview получает `SAME`. Для Helm безопасный replay использует ранее `VERIFIED` SOURCE→TARGET digest pair: текущий TARGET OCI digest должен совпасть с тем, который портал зафиксировал при успешном импорте того же SOURCE digest. Такие artifacts execution сохраняет как `SKIPPED` без повторной mutation Harbor. Если TARGET digest изменился вне этого verified pair, результат снова `CONFLICT`.

## Receipt

После execution формируется receipt в двух местах:

- JSON snapshot в operation record;
- immutable file `IMPORT_RECEIPT_ROOT/import-<operation_id>.json`, создаваемый exclusive-write (`x`) и переводимый в read-only mode.

Receipt содержит source delivery id, exact bundle SHA256, actor username, execution timestamps, выбранную overwrite policy и per-artifact outcomes/digests/errors. Для Helm `expected_digest` остаётся SOURCE OCI provenance, а `target_digest` содержит фактически наблюдаемый TARGET OCI digest. Credentials, Harbor password, auth files и secret material туда не записываются.

Receipt формируется и для partial failure. Если execution не начался из-за invalid bundle/conflict/unknown policy, receipt отсутствует, а причина остаётся в operation error semantics.

Frontend может получить небольшой receipt через `GET /api/imports/{id}/receipt` и сохранить JSON для оператора. Это представление не заменяет immutable backend receipt file.

## Настройки

| Setting | Default | Назначение |
|---|---|---|
| `IMPORT_DISCOVERY_ROOT` | `./data/incoming` | каталог для готовых offline bundles |
| `IMPORT_STAGING_ROOT` | `./data/incoming/staged` | private server-generated staging |
| `IMPORT_RECEIPT_ROOT` | `./data/receipts/imports` | immutable import receipts |
| `IMPORT_MAX_UPLOAD_BYTES` | `50 GiB` | hard intake size limit |
| `IMPORT_STREAM_CHUNK_BYTES` | `1 MiB` | рекомендуемый application chunk size для файловых операций |
| `IMPORT_ALLOW_OVERWRITE` | `false` | server-side permission для explicit conflict overwrite |
| `IMPORT_BUNDLE_RETENTION_SECONDS` | `604800` | retention terminal failed/partial bundle для retry/диагностики |
| `STORAGE_CLEANUP_INTERVAL_SECONDS` | `3600` | период startup/periodic storage cleanup |

После успешного import staging bundle удаляется сразу, extraction workspace удаляется после worker независимо от результата. Failed/partial bundle сохраняется до retention, чтобы retry мог повторно проверить и распаковать исходный physical payload. Shared `storage_key` не удаляется, пока хотя бы одна связанная operation non-terminal. Подробнее: [storage-retention.md](storage-retention.md).

Bundle archive/extraction/member/compression limits дополнительно задаются общими `BUNDLE_*` settings и применяются verifier path.

## API

- `POST /api/imports/upload` — streaming intake;
- `POST /api/imports/discover` — claim готовых archive + `.sha256` pairs;
- `GET /api/imports/{operation_id}/preview` — persisted verified preview + signed SOURCE/policy projection;
- `POST /api/imports/{operation_id}/execute` — explicit import policy + background start;
- `GET /api/imports/{operation_id}/receipt` — финальный import receipt;
- `GET /api/operations/{operation_id}` — generic status/progress/artifact outcomes;
- `POST /api/operations/{operation_id}/cancel` — generic cancellation с owner/admin policy.

## Проверяемые инварианты

Regression suite фиксирует следующие свойства:

- signed mixed image+chart bundle проходит preview/import и создаёт receipt;
- verified preview projection берёт SOURCE metadata из signed manifest и подтверждает checksum/schema/signature только после verifier;
- legacy persisted preview остаётся parseable с безопасными false/null defaults;
- `SAME` не вызывает повторный image/chart push;
- Helm replay признаётся `SAME` только для ранее `VERIFIED` SOURCE→TARGET digest pair, а внешняя замена TARGET digest остаётся `CONFLICT`;
- Helm post-push проверяет signed `.tgz payload_sha256`, сохраняя реальный TARGET OCI digest отдельно;
- conflict блокируется по умолчанию, overwrite требует server policy и отдельного UI confirmation;
- corrupt/invalid bundle отклоняется до target inspection;
- streaming hard limit удаляет partial upload;
- incoming archive без sidecar не claim-ится;
- изменение bundle после preview обнаруживается до import;
- image post-import digest mismatch приводит к FAILED и отражается в receipt;
- browser reload восстанавливает active import operation;
- SOURCE contour не показывает normal TARGET workflow и backend отклоняет import intake.
