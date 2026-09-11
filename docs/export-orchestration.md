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
- `reference` — image tag либо Helm version/tag;
- `digest` — SHA-256, полученный из Harbor browse API.

Preview повторно читает локальный Harbor и не доверяет браузеру как источнику digest. Если artifact исчез, изменил digest или тип, запрос отклоняется стабильным error code.

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

Эти значения сохраняются в БД только после успешной verified публикации.

### Bundle metadata и download

- `GET /api/exports/{operation_id}/bundle`;
- `GET /api/exports/{operation_id}/download`.

Operator может читать только собственный export bundle; admin может читать любой. Download использует `FileResponse`, поэтому backend не загружает весь archive в память. Ответ содержит стандартный `Content-Length` и `X-Checksum-SHA256`.

Имя файла всегда выводится из generated `delivery_id`; repository, comment и другие пользовательские строки не участвуют в filesystem path.

## Workflow

Worker выполняет следующие стадии:

1. `VALIDATING` — повторно проверяет selection и pinned digest;
2. `RUNNING` — последовательно экспортирует выбранные artifacts;
3. container image проходит через Skopeo service;
4. Helm chart проходит через Helm OCI service;
5. digest результата снова сравнивается с pinned SOURCE digest;
6. `PACKAGING` — BundlePackageService строит canonical Offline Bundle v1;
7. BundlePackageService проверяет manifest schema, checksums и Ed25519 signature до готовности публикации;
8. archive публикуется атомарно, readiness `.sha256` создаётся последним;
9. `VERIFYING` — orchestration проверяет delivery identity и фиксирует bundle metadata;
10. artifact rows переходят в `VERIFIED`;
11. только после этого операция получает `COMPLETED`.

Для v1 выбран fail-fast режим. Частичный delivery не считается успешным.

## Защита от изменения SOURCE

Digest проверяется минимум в двух независимых точках:

- при preview/start через Harbor API;
- непосредственно в Skopeo/Helm export primitive.

Если artifact изменился между выбором и фактической выгрузкой, операция получает `FAILED`, а другой объект не подставляется молча в manifest.

## Публикация и cancellation barrier

Packaging выполняется в worker thread через `asyncio.to_thread`, чтобы не блокировать event loop. Отмена asyncio task сама по себе не останавливает такой thread.

Поэтому orchestration использует cancellation barrier:

- при cancel во время packaging backend ждёт завершения уже запущенного packaging thread;
- возможные archive/readiness sidecar удаляются до завершения cancellation;
- persisted bundle metadata очищается;
- только после cleanup OperationManager фиксирует terminal `CANCELLED`.

Это предотвращает состояние, в котором отменённая операция оставляет delivery, выглядящий готовым к переносу.

## Fail-fast semantics

Если любой artifact export завершается ошибкой:

- текущий artifact получает `FAILED`;
- ещё не начатые artifacts получают `FAILED / export_aborted`;
- ранее выполнявшиеся artifact rows не превращают delivery в успешный;
- packaging не запускается либо его публикация удаляется;
- operation завершается `FAILED`.

Ошибка проверки/подписи bundle также не оставляет readiness sidecar.

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

- mixed image + Helm happy path;
- изменение SOURCE digest между preview и worker;
- fail-fast при ошибке одного artifact;
- package/verification failure без ready bundle;
- cancellation во время packaging без оставшейся публикации;
- viewer RBAC;
- TARGET contour rejection;
- owner-scoped download;
- `Content-Length` и SHA-256 metadata;
- projection persisted bundle metadata через generic operation API.
