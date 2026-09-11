# SOURCE export orchestration

Статус: **актуальный component document** для backend flow задачи #17.

Документ описывает только уже реализованный backend-контур экспорта. Пользовательский wizard frontend в эту задачу не входит.

## Назначение

SOURCE export orchestration связывает уже существующие компоненты портала в один fail-fast workflow:

1. Harbor browse/client — повторно разрешает выбранные stable artifact identifiers;
2. Skopeo service — выгружает container image в OCI image-layout;
3. Helm OCI service — получает Helm chart package;
4. BundlePackageService — строит, подписывает и проверяет Offline Bundle v1;
5. OperationManager — выполняет workflow в фоне, хранит прогресс, ошибки и cancellation state;
6. controlled outgoing storage — содержит только завершённый delivery archive и readiness sidecar.

Длинная передача не выполняется внутри одного HTTP request. `POST /api/exports` возвращает `202` после создания persistent operation и постановки worker в очередь.

## API

### Preview

`POST /api/exports/preview`

Доступ: `operator`, `admin`. Контур: только `SOURCE`.

Request содержит массив выбранных артефактов:

- `kind` — `container-image` или `helm-chart`;
- `project`;
- `repository`;
- `reference` — image tag/digest либо Helm version/tag;
- `digest` — SHA-256, полученный из Harbor browse API.

Preview повторно читает локальный Harbor и не доверяет браузеру как источнику digest или artifact kind. Один и тот же `project/repository/reference` нельзя передать дважды, даже с разными digest. Image reference и Helm version проходят те же синтаксические ограничения, что downstream Skopeo/Helm DTO.

Если artifact исчез, изменил digest или тип, запрос отклоняется стабильным error code.

### Start

`POST /api/exports`

Доступ: `operator`, `admin`. Контур: только `SOURCE`.

Перед созданием операции selection разрешается повторно, выполняется disk preflight, затем создаются `Operation` и persisted artifact rows. Ответ содержит `operation_id` и сгенерированный `delivery_id`.

`viewer` не может запускать или preview export.

### Статус и отмена

Используются generic endpoints OperationManager:

- `GET /api/operations/{operation_id}`;
- `POST /api/operations/{operation_id}/cancel`.

После успешного export поле `bundle` generic operation response содержит:

- `filename`;
- `size_bytes`;
- `sha256`.

Bundle metadata также используется как internal ownership marker в коротком окне публикации: после атомарного создания archive, но до readiness sidecar. Наружу готовый bundle всё равно выдаётся только после terminal `COMPLETED` и повторной проверки файловой metadata.

### Bundle metadata и download

- `GET /api/exports/{operation_id}/bundle`;
- `GET /api/exports/{operation_id}/download`.

Operator может читать только собственный export bundle; admin может читать любой. Download использует `FileResponse`, поэтому backend не загружает весь archive в память. Ответ содержит стандартный `Content-Length` и `X-Checksum-SHA256`.

Имя файла всегда выводится из generated `delivery_id`; repository, comment и другие пользовательские строки не участвуют в filesystem path. Перед выдачей backend повторно сверяет persisted filename/size/SHA-256 с archive и exact readiness sidecar.

## Workflow

Worker выполняет следующие стадии:

1. `VALIDATING` — повторно проверяет selection и pinned digest;
2. `RUNNING` — последовательно экспортирует выбранные artifacts;
3. container image проходит через Skopeo service;
4. Helm chart проходит через Helm OCI service;
5. digest результата снова сравнивается с pinned SOURCE digest;
6. `PACKAGING` — BundlePackageService строит canonical Offline Bundle v1;
7. BundlePackageService проверяет manifest schema, checksums и Ed25519 signature до готовности публикации;
8. archive публикуется с atomic no-replace semantics;
9. ownership metadata (`filename`, `sha256`, `size`) фиксируется для текущей operation;
10. readiness `.sha256` публикуется последним и тоже не может перезаписать существующий файл;
11. `VERIFYING` — orchestration проверяет delivery identity, controlled paths, sidecar digest/size и подтверждает bundle metadata;
12. artifact rows переходят в `VERIFIED`;
13. только после этого операция получает `COMPLETED`.

Для v1 выбран fail-fast режим. Частичный delivery не считается успешным.

## Защита от изменения SOURCE

Digest проверяется в нескольких независимых точках:

- при preview/start через Harbor API;
- повторно worker перед transfer;
- в Skopeo/Helm export primitive после фактической выгрузки.

Если artifact изменился между выбором и фактической выгрузкой, операция получает `FAILED`, а другой объект не подставляется молча в manifest.

## Публикация и cancellation barrier

Packaging выполняется в worker thread через `asyncio.to_thread`, чтобы не блокировать event loop. Отмена asyncio task сама по себе не останавливает такой thread.

Поэтому orchestration использует cancellation barrier:

- при cancel во время packaging backend ждёт завершения уже запущенного packaging thread;
- cleanup удаляет archive/readiness sidecar только если ownership текущей operation подтверждён persisted metadata;
- unowned pre-existing delivery при collision не удаляется и не перезаписывается;
- persisted ownership metadata очищается вместе с rollback;
- только после cleanup OperationManager фиксирует terminal `CANCELLED`.

Это предотвращает одновременно два класса ошибок: отменённая операция не оставляет собственный delivery, выглядящий готовым к переносу, и не может удалить уже существующий чужой delivery с совпавшим generated id.

## Crash/restart recovery publication boundary

Существует отдельное окно между физической публикацией archive и terminal commit `COMPLETED`. Если процесс аварийно завершится именно в этот момент, одних restart semantics OperationManager недостаточно: на диске мог бы остаться partial либо ready-looking delivery.

Поэтому application startup до `OperationManager.startup()` выполняет reconciliation SOURCE export publications:

- выбирает export operations, которые не находятся в `COMPLETED`;
- считает publication принадлежащей operation только при наличии persisted ownership metadata;
- для owned publication удаляет generated archive/readiness sidecar из controlled outgoing root и очищает metadata;
- совпавший delivery без ownership metadata считается внешним/pre-existing и не удаляется;
- incomplete/partial metadata очищается без удаления неподтверждённых файлов;
- завершённые `COMPLETED` delivery не трогает.

После этого обычная OperationManager reconciliation переводит interrupted active operation в `FAILED`. Автоматический resume export v1 не поддерживается.

## Fail-fast semantics

Если любой artifact export завершается ошибкой:

- текущий artifact получает `FAILED`;
- ещё не начатые artifacts получают `FAILED / export_aborted`;
- ранее выполнявшиеся artifact rows не превращают delivery в успешный;
- packaging не запускается либо owned publication удаляется;
- operation завершается `FAILED`.

Ошибка проверки/подписи bundle также не оставляет readiness sidecar текущей operation.

## Stable error semantics

Основные export-owned коды:

- `export_wrong_contour` — endpoint вызван не на SOURCE;
- `export_source_not_found` — выбранный SOURCE artifact исчез;
- `export_source_changed` — authoritative digest изменился;
- `export_artifact_kind_changed` — Harbor теперь классифицирует artifact иначе;
- `export_artifact_unsupported` — `unknown-oci` или иной неподдерживаемый type;
- `export_aborted` — ещё не начатый artifact остановлен fail-fast политикой;
- `export_not_ready` — bundle запрошен до `COMPLETED`;
- `export_bundle_missing` — persisted completed delivery отсутствует на диске;
- `export_bundle_metadata_invalid` — persisted/file/sidecar metadata расходятся;
- `export_bundle_path_invalid` — path publication не соответствует controlled delivery location.

Harbor/Skopeo/Helm/BundlePackageService ошибки сохраняют собственные безопасные стабильные коды. Raw stderr/upstream body/secrets наружу не проксируются.

## Storage boundary

Export использует только server-controlled roots из `Settings`:

- OperationManager workspace;
- Skopeo payload root;
- Helm workspace root;
- BundlePackageService outgoing root.

Download заново проверяет, что generated archive path остаётся внутри `bundle_outgoing_root`. Persisted filename обязан точно соответствовать `delivery_id`.

## Ограничения v1

- export wizard frontend не входит в #17;
- automatic resume interrupted export после restart не поддерживается — OperationManager завершает активную non-resumable операцию как `FAILED`;
- неизвестный OCI artifact type (`unknown-oci`) export v1 не поддерживает;
- flow допускает artifacts из нескольких Harbor projects/repositories в одном delivery, если каждый selection успешно проходит validation.

## Проверки

Regression suite покрывает:

- mixed image + Helm happy path с реальным BundlePackageService/signature verification;
- изменение SOURCE digest между preview и worker;
- logical duplicate selection и invalid reference;
- fail-fast при ошибке одного artifact;
- package/verification failure без ready bundle;
- cancellation во время packaging без оставшейся owned publication;
- no-replace collision для archive и sidecar;
- сохранность unowned pre-existing delivery при runtime cleanup и startup recovery;
- startup cleanup persisted owned publication;
- viewer RBAC;
- TARGET contour rejection;
- owner-scoped download;
- `Content-Length` и SHA-256 metadata;
- projection persisted bundle metadata через generic operation API.
