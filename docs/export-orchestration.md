# SOURCE export orchestration

Статус: **актуальный component/API document** для backend flow #17 и browser delivery integration #18.

SOURCE export backend orchestration реализован. Пользовательский 4-step wizard реализован отдельно в `frontend/src/views/ExportView.vue`; этот документ фиксирует backend contract, publication boundary и download semantics, на которые он опирается.

## Назначение

SOURCE export связывает следующие компоненты в один fail-fast workflow:

1. Harbor browse/client — разрешает stable artifact identifiers и authoritative digest;
2. Skopeo service — выгружает container image в OCI image-layout;
3. Helm OCI service — получает Helm chart package;
4. BundlePackageService — строит, подписывает и проверяет Offline Bundle v1;
5. OperationManager — выполняет работу в фоне и сохраняет progress/cancel/error state;
6. controlled outgoing storage — содержит только завершённые owned delivery archive и readiness sidecar;
7. export API — отдаёт preview/start/status metadata и disk-backed download.

Длинная передача не выполняется внутри одного HTTP request. `POST /api/exports` возвращает `202` после создания persisted operation и постановки worker в очередь.

## API

### Preview

`POST /api/exports/preview`

Доступ: `operator`, `admin`. Контур: только `SOURCE`.

Request содержит массив выбранных artifacts:

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

Перед созданием операции selection разрешается повторно и выполняется disk preflight. Затем создаются `Operation` и persisted artifact rows. Ответ содержит `operation_id` и server-generated `delivery_id`.

`viewer` не может preview/start export.

### Статус и отмена

Используются generic OperationManager endpoints:

- `GET /api/operations/{operation_id}`;
- `POST /api/operations/{operation_id}/cancel`.

После успешного export поле `bundle` generic operation response содержит `filename`, `size_bytes` и `sha256`. Эти значения становятся пользовательской ready metadata только после verified publication и terminal `COMPLETED`.

### Bundle metadata

`GET /api/exports/{operation_id}/bundle`

Доступ: owner `operator` либо `admin`. Ответ доступен только для `COMPLETED` export и содержит:

- `delivery_id`;
- `archive_name`;
- `archive_size`;
- SHA-256;
- canonical download path.

Перед выдачей backend повторно сверяет persisted filename/size/SHA-256 с archive и exact readiness sidecar.

### Browser download ticket

`POST /api/exports/{operation_id}/download-ticket`

Endpoint нужен для безопасного native browser download большого archive. Access JWT frontend хранит в `sessionStorage` и добавляет в Axios `Authorization`; обычная browser navigation не может использовать этот interceptor, а скачивание через Axios `blob` потребовало бы буферизации потенциально большого bundle в памяти браузера.

Поэтому flow такой:

1. authenticated frontend вызывает `POST .../download-ticket` с bearer token;
2. backend повторно проверяет, что export `COMPLETED`, bundle metadata валидна и actor является owner либо admin;
3. backend выдаёт короткоживущий JWT типа `export-download`, привязанный к `user_id` и конкретному `operation_id`;
4. token устанавливается как `HttpOnly`, `SameSite=Strict` cookie с `Path=/api/exports/{id}/download`, TTL 120 секунд и `Secure` на HTTPS;
5. frontend запускает обычный browser navigation на download URL;
6. download endpoint ещё раз проверяет token, operation binding, активного пользователя и ownership.

Ticket не является общей web-session cookie и не должен давать доступ к другому export operation.

### Download

`GET /api/exports/{operation_id}/download`

Поддерживаются два способа authorization:

- обычный bearer token;
- scoped browser download ticket для конкретного operation.

Download использует `FileResponse`, поэтому backend не загружает archive целиком в память. Ответ содержит `Content-Length`, `X-Checksum-SHA256` и `Cache-Control: no-store`.

Имя файла всегда выводится из generated `delivery_id`; repository, comment и другие пользовательские строки не участвуют в filesystem path.

## Workflow

Worker выполняет стадии:

1. `VALIDATING` — повторно проверяет selection и pinned digest;
2. `RUNNING` — последовательно экспортирует artifacts;
3. container image проходит через Skopeo service;
4. Helm chart проходит через Helm OCI service;
5. digest результата сравнивается с pinned SOURCE digest;
6. `PACKAGING` — BundlePackageService строит canonical Offline Bundle v1;
7. package service проверяет schema, checksums и Ed25519 signature;
8. archive публикуется с atomic no-replace semantics;
9. ownership metadata (`filename`, `sha256`, `size`) фиксируется для текущей operation;
10. readiness `.sha256` публикуется последним и тоже не может перезаписать существующий файл;
11. `VERIFYING` — orchestration проверяет delivery identity, controlled paths, sidecar digest/size и подтверждает bundle metadata;
12. artifact rows переходят в `VERIFIED`;
13. только после этого operation получает `COMPLETED`.

Для v1 выбран fail-fast режим. Частичный delivery не считается успешным.

## Защита от изменения SOURCE

Digest проверяется в нескольких независимых точках:

- при preview/start через Harbor API;
- повторно worker перед transfer;
- в Skopeo/Helm primitive после фактической выгрузки.

Если artifact изменился между выбором и transfer, operation получает `FAILED`; другой объект не подставляется молча в manifest.

## Publication ownership и no-replace

Generated `delivery_id` не даёт права перезаписать уже существующий файл. Publication guard обеспечивает:

- archive и sidecar создаются с no-replace semantics;
- collision не перезаписывает existing delivery;
- cleanup удаляет publication только при подтверждённой ownership текущей operation;
- unowned pre-existing archive/sidecar сохраняются;
- persisted ownership metadata используется как marker между archive publication и terminal commit.

Это закрывает TOCTOU/collision сценарий между проверкой существования path и фактической публикацией.

## Cancellation barrier

Packaging выполняется в worker thread через `asyncio.to_thread`. Отмена asyncio task сама по себе не останавливает такой thread.

Поэтому при cancel во время packaging backend:

- ждёт завершения уже запущенного packaging thread;
- удаляет archive/readiness sidecar только при подтверждённой ownership текущей operation;
- очищает persisted ownership metadata;
- только после cleanup фиксирует terminal `CANCELLED`.

Отменённая operation не должна оставлять собственный ready-looking delivery и не должна удалить чужой delivery при collision.

## Crash/restart recovery

Существует окно между физической publication и terminal `COMPLETED`. При startup до `OperationManager.startup()` выполняется reconciliation export publications:

- выбираются export operations не в `COMPLETED`;
- publication считается owned только при валидной persisted ownership metadata;
- owned archive/readiness sidecar удаляются и metadata очищается;
- unowned совпавший delivery не удаляется;
- incomplete metadata очищается без удаления неподтверждённых файлов;
- `COMPLETED` deliveries не трогаются.

После этого обычная OperationManager reconciliation переводит interrupted active operation в `FAILED`. Transparent resume export v1 не поддерживается.

## Fail-fast semantics

Если любой artifact export завершается ошибкой:

- текущий artifact получает `FAILED`;
- ещё не начатые artifacts получают `FAILED / export_aborted`;
- ранее обработанные rows не превращают delivery в успешный;
- packaging не запускается либо owned publication удаляется;
- operation завершается `FAILED`.

Ошибка package verification/signature также не оставляет owned readiness sidecar.

## Stable error semantics

Основные export-owned codes:

- `export_wrong_contour` — endpoint вызван не на SOURCE;
- `export_source_not_found` — выбранный SOURCE artifact исчез;
- `export_source_changed` — authoritative digest изменился;
- `export_artifact_kind_changed` — Harbor классифицирует artifact иначе;
- `export_artifact_unsupported` — неподдерживаемый type;
- `export_aborted` — artifact остановлен fail-fast policy;
- `export_not_ready` — bundle запрошен до `COMPLETED`;
- `export_bundle_missing` — completed delivery отсутствует на диске;
- `export_bundle_metadata_invalid` — persisted/file/sidecar metadata расходятся;
- `export_bundle_path_invalid` — path не соответствует controlled delivery location;
- `download_auth_required` — native download вызван без bearer/ticket;
- `download_auth_invalid` — ticket/token недействителен, истёк либо не соответствует operation;
- `export_forbidden` — actor не имеет доступа к export operation.

Harbor/Skopeo/Helm/BundlePackageService errors сохраняют безопасные стабильные codes. Raw stderr/upstream body/secrets наружу не проксируются.

## Storage boundary

Export использует только server-controlled roots из `Settings`:

- OperationManager workspace;
- Skopeo payload root;
- Helm workspace root;
- BundlePackageService outgoing root.

Download заново проверяет, что generated archive path остаётся внутри `bundle_outgoing_root`. Persisted filename обязан точно соответствовать `delivery_id`.

## Frontend integration

4-step SOURCE wizard #18 использует только public contracts:

1. Harbor browse API → exact selection;
2. `/exports/preview` → authoritative preview;
3. `/exports` + `/operations/{id}` → start/progress/cancel/reload;
4. `/exports/{id}/bundle` + download ticket → ready/download.

Operation id сохраняется в browser `sessionStorage`, поэтому refresh не теряет background operation. UI не вычисляет ETA и не предлагает download для `FAILED/CANCELLED`.

Подробнее: [frontend.md](frontend.md).

## Ограничения v1

- automatic resume interrupted export после restart не поддерживается;
- `unknown-oci` export v1 не поддерживается;
- один delivery может содержать artifacts из нескольких Harbor projects/repositories, если каждый selection проходит validation;
- download ticket короткоживущий и предназначен только для same-origin browser download, а не как reusable API credential.

## Проверки

Regression suite покрывает:

- mixed image + Helm happy path с реальным BundlePackageService/signature verification;
- изменение SOURCE digest между preview и worker;
- duplicate selection и invalid reference;
- fail-fast при ошибке одного artifact;
- package/verification failure без ready bundle;
- cancellation во время packaging;
- no-replace collision archive/sidecar;
- сохранность unowned pre-existing delivery;
- startup cleanup owned incomplete publication;
- viewer/TARGET guards;
- owner-scoped metadata/download;
- browser download ticket без bearer на native GET;
- ticket mint authorization;
- `Content-Length`, SHA-256 и no-store metadata;
- frontend selection/preview/progress/reload/ready flow.
