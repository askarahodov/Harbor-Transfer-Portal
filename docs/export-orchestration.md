# SOURCE export orchestration API

Этот документ описывает реализованный backend workflow задачи #17. Он связывает уже существующие
Harbor browse/client, Skopeo, Helm OCI, Bundle Protocol v1 и persistent `OperationManager`.
Frontend export wizard относится к #18 и здесь не описывается как готовая функция.

## Назначение и границы

Export workflow доступен только экземпляру портала с `PORTAL_CONTOUR=SOURCE`.

Запускать validation/export могут роли `admin` и `operator`. `viewer` получает `403` на export
endpoints. Статус и отмена используют общий API операций из #16:

- `GET /api/operations/{operation_id}`;
- `POST /api/operations/{operation_id}/cancel`.

Operator может получать готовый bundle только для своей операции, admin — для любой завершённой
export-операции. Bundle нельзя получить до статуса `COMPLETED`.

## API

### `POST /api/exports/validate`

Проверяет selection без создания `Operation`. Для каждого выбранного artifact backend повторно
читает authoritative metadata локального Harbor и возвращает digest и известный размер.

Container image:

```json
{
  "type": "container-image",
  "project": "platform",
  "repository": "app/backend",
  "reference": "1.4.2"
}
```

Helm OCI chart:

```json
{
  "type": "helm-chart",
  "project": "platform",
  "repository": "charts/gateway",
  "version": "2.3.0"
}
```

Ответ содержит `source_digest`. Клиент должен передать именно этот digest в последующий start
request. Это связывает пользовательский preview с конкретным состоянием SOURCE Harbor.

Один delivery может содержать artifacts из нескольких Harbor projects. Duplicate selection одного
и того же type/project/repository/reference-or-version отклоняется.

`comment` необязателен и ограничен 2000 символами контрактом Bundle Protocol v1.

### `POST /api/exports`

Создаёт persisted export operation и сразу возвращает `202 Accepted`, `operation_id`,
`delivery_id`, URL статуса и URL будущего bundle metadata. Долгая работа выполняется через
`OperationManager`, а HTTP request не ждёт Skopeo/Helm/package steps.

Для start каждый artifact обязан содержать `source_digest`, полученный validation endpoint.
Backend ещё раз читает Harbor. Если digest уже изменился, operation не создаётся и возвращается
`export_source_changed`.

### Готовый delivery

После `COMPLETED` доступны:

- `GET /api/exports/{operation_id}/bundle` — metadata;
- `GET /api/exports/{operation_id}/bundle/download` — archive;
- `GET /api/exports/{operation_id}/bundle/checksum` — `.sha256` sidecar.

Файлы отдаются с диска через `FileResponse`; backend не читает весь archive в память.
Имена строятся только из server-generated `delivery_id`:

```text
DELIVERY-YYYYMMDD-<RANDOM>.htp.tar.gz
DELIVERY-YYYYMMDD-<RANDOM>.htp.tar.gz.sha256
```

User comment, Harbor repository и browser filename не участвуют в filesystem path.

## Worker flow

Persisted export проходит state machine:

```text
CREATED → VALIDATING → RUNNING → PACKAGING → VERIFYING → COMPLETED
```

При ошибке или отмене используются существующие `FAILED` / `CANCELLED`.

### VALIDATING

Worker:

1. проверяет disk reserve через #16;
2. создаёт private per-operation workspace;
3. проверяет, что effective SOURCE Harbor URL не изменился после API validation.

Known Harbor artifact sizes используются только как честный нижний bound для disk preflight.
Неизвестный размер не заменяется выдуманной оценкой.

### RUNNING

Перед каждым artifact backend снова читает Harbor digest. Это защита от изменения source между
start request и фактическим worker execution.

Container image экспортируется существующим `SkopeoService.export_image()`. Сам Skopeo service:

- ещё раз inspect-ит SOURCE;
- выполняет `skopeo copy --all --preserve-digests`;
- проверяет OCI image-layout;
- сверяет digest локального payload с SOURCE digest.

Helm chart экспортируется `HelmOciService.pull_chart()`. Service получает digest из Harbor,
выполняет `helm pull` и проверяет name/version внутри chart package.

Orchestration дополнительно сравнивает digest, возвращённый Skopeo/Helm, с digest selection.
Изменение source не маскируется обновлением manifest.

Payload paths генерируются backend:

```text
images/artifact-0001/
charts/artifact-0002.tgz
```

Raw repository/comment никогда не используется как имя staging path.

### Fail-fast v1

Export delivery является release unit, поэтому v1 использует fail-fast:

- текущий artifact получает `FAILED`;
- ещё не запущенные artifacts получают `SKIPPED` с `export_aborted_fail_fast`;
- `Operation` получает `FAILED`;
- package/published delivery не создаётся.

Успешно экспортированный payload может иметь artifact status `VERIFIED`, даже если последующий
package step завершился ошибкой. Источником истины о готовности delivery остаётся operation
`COMPLETED` и наличие опубликованной пары archive + sidecar.

### PACKAGING и VERIFYING

`BundlePackageService` выполняет protocol/security работу #15:

- формирует canonical manifest;
- считает payload checksums;
- подписывает Ed25519;
- создаёт deterministic archive;
- прогоняет новый archive через тот же verifier path;
- только после успешной self-verification создаёт staged archive + sidecar.

Orchestration запускает этот sync filesystem/crypto участок вне event loop. При cancellation backend
дожидается окончания уже начатой non-interruptible filesystem операции, прежде чем разрешить
`OperationManager` удалить workspace. Это предотвращает race «thread ещё пишет в удалённый
workspace».

После проверки staged result archive переносится в configured `bundle_outgoing_root`.
Сначала публикуется archive, затем `.sha256` sidecar как финальный readiness marker. Для
cross-filesystem move используется bounded streaming copy во временный hidden file + `fsync` +
atomic `os.replace`; archive целиком в RAM не загружается.

Если publication или последующий completion не состоялись, файлы, созданные именно этим worker,
удаляются. Уже существующий delivery с совпавшим generated id не удаляется.

## Security invariants

- SOURCE workflow недоступен на TARGET;
- viewer не запускает и не скачивает export;
- Harbor credentials, JWT secret и signing private key не попадают в request/manifest/path;
- Skopeo/Helm получают аргументы как argv, не shell fragments;
- authoritative digest проверяется на preview, start и непосредственно перед artifact export;
- package self-verification обязателен до final publication;
- symlink и path escape для staged/published bundle запрещены;
- partial/failed delivery не выдаётся как ready;
- download path восстанавливается по persisted server-generated `delivery_id`, а не принимается от
  клиента.

## Что остаётся за другими задачами

- #18 — SOURCE export wizard UI;
- #19 — TARGET intake/preview/import orchestration;
- #21 — полный audit/history/correlation слой;
- #25 — CSV/PDF reports и receipts.

Этот API не утверждает, что end-to-end SOURCE → TARGET workflow уже завершён.
