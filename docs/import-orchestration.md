# TARGET import orchestration

Статус: **актуальный component document** для backend TARGET import flow.

Этот документ описывает реализацию `P5.1` и дополняет нормативный [Offline Bundle v1](offline-bundle-v1.md), [OperationManager](operation-manager.md), [Skopeo service](skopeo-service.md) и [Helm OCI service](helm-oci-service.md). Он не переопределяет формат bundle или криптографический trust contract.

## Назначение

TARGET import orchestration принимает готовый Offline Bundle v1, проверяет его до любых изменений Harbor, строит persisted preview целевого состояния, применяет явную conflict policy и только после этого запускает background import.

HTTP request не выполняет долгий OCI transfer. Intake и запуск возвращают operation id, а дальнейшее состояние читается через generic `/api/operations/{id}` и import-specific preview/receipt endpoints.

## Контур и роли

Import API работает только при `PORTAL_CONTOUR=TARGET`.

Доступ к `/api/imports/*` имеют `operator` и `admin`. Обычный operator может читать preview/receipt и запускать import только для собственной operation. `admin` может работать с любой import operation. `viewer` не получает import endpoints через RBAC dependency.

SOURCE-инстанс отвечает `import_wrong_contour` и не создаёт staging bundle.

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

### Incoming discovery

`POST /api/imports/discover` сканирует только файлы `*.htp.tar.gz` непосредственно в `IMPORT_DISCOVERY_ROOT`.

Bundle считается готовым к claim только когда рядом существует обычный файл `<bundle>.sha256`. Архив без readiness sidecar игнорируется, поэтому копируемый или ещё не финализированный файл не попадает в verification.

Готовая пара archive + sidecar атомарно перемещается в server-generated staging directory, после чего создаётся `IMPORT/DISCOVERED` operation.

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

## Persisted preview

Для каждого manifest artifact TARGET Harbor инспектируется без mutation.

Классификации:

| Состояние | Значение |
|---|---|
| `NEW` | соответствующего target reference/version нет; можно импортировать |
| `SAME` | target существует и digest совпадает с source expectation; повторный import должен быть skip |
| `CONFLICT` | target reference/version существует с другим digest |
| `UNKNOWN` | target существует, но manifest не позволяет доказать equality по digest |
| `ERROR` | target inspection не удалось безопасно выполнить |

Preview сохраняется в DB вместе с exact bundle SHA256, source delivery id, размером и fingerprint signing key. Artifact rows создаются в manifest order и затем используются generic operation status API.

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

Helm charts валидируются и push-ятся через Helm OCI adapter. При наличии source digest итоговый TARGET digest обязан совпасть с manifest expectation.

Независимые artifacts обрабатываются последовательно и имеют отдельные outcomes. Ошибка одного artifact не приводит к попытке отката уже успешно импортированного другого artifact. Portal не заявляет atomic rollback across Harbor artifacts.

Operation становится `COMPLETED` только если policy успешно выполнена для всех artifacts. При частичном сбое итоговая operation — `FAILED`, а уже выполненные per-artifact результаты сохраняются.

## Idempotency и replay

Повторная доставка того же bundle может создать новую import operation: `source_delivery_id` намеренно не unique. Bundle identity фиксируется через SHA256.

Если target уже содержит ожидаемые digests, новый preview классифицирует artifacts как `SAME`, а execution сохраняет их как `SKIPPED`. Это безопасный replay и не требует повторной mutation Harbor.

## Receipt

После execution формируется receipt в двух местах:

- JSON snapshot в operation record;
- immutable file `IMPORT_RECEIPT_ROOT/import-<operation_id>.json`, создаваемый exclusive-write (`x`) и переводимый в read-only mode.

Receipt содержит source delivery id, exact bundle SHA256, actor username, execution timestamps, выбранную overwrite policy и per-artifact outcomes/digests/errors. Credentials, Harbor password, auth files и secret material туда не записываются.

Receipt формируется и для partial failure. Если execution не начался из-за invalid bundle/conflict/unknown policy, receipt отсутствует, а причина остаётся в operation error semantics.

## Настройки

| Setting | Default | Назначение |
|---|---|---|
| `IMPORT_DISCOVERY_ROOT` | `./data/incoming` | каталог для готовых offline bundles |
| `IMPORT_STAGING_ROOT` | `./data/incoming/staged` | private server-generated staging |
| `IMPORT_RECEIPT_ROOT` | `./data/receipts/imports` | immutable import receipts |
| `IMPORT_MAX_UPLOAD_BYTES` | `50 GiB` | hard intake size limit |
| `IMPORT_STREAM_CHUNK_BYTES` | `1 MiB` | рекомендуемый application chunk size для файловых операций |
| `IMPORT_ALLOW_OVERWRITE` | `false` | server-side permission для explicit conflict overwrite |

Bundle archive/extraction/member/compression limits дополнительно задаются общими `BUNDLE_*` settings и применяются verifier path.

## API

- `POST /api/imports/upload` — streaming intake;
- `POST /api/imports/discover` — claim готовых archive + `.sha256` pairs;
- `GET /api/imports/{operation_id}/preview` — persisted verified preview;
- `POST /api/imports/{operation_id}/execute` — explicit import policy + background start;
- `GET /api/imports/{operation_id}/receipt` — финальный import receipt;
- `GET /api/operations/{operation_id}` — generic status/progress/artifact outcomes;
- `POST /api/operations/{operation_id}/cancel` — generic cancellation с owner/admin policy.

## Проверяемые инварианты

Regression suite фиксирует следующие свойства:

- signed mixed image+chart bundle проходит preview/import и создаёт receipt;
- `SAME` не вызывает повторный image/chart push;
- conflict блокируется по умолчанию, overwrite требует server policy;
- corrupt/invalid bundle отклоняется до target inspection;
- streaming hard limit удаляет partial upload;
- incoming archive без sidecar не claim-ится;
- изменение bundle после preview обнаруживается до import;
- post-import digest mismatch приводит к FAILED и отражается в receipt;
- SOURCE contour отклоняет import intake.
