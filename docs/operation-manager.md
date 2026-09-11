# Менеджер фоновых операций

`OperationManager` реализует базовый worker-слой Harbor Transfer Portal v1 для длительных export/import операций. Он рассчитан на исходную архитектуру проекта: один backend-процесс, SQLite и отсутствие обязательных Redis/Celery.

## Что делает менеджер

- создаёт `Operation` и `ArtifactResult` до запуска работы;
- запускает coroutine-worker через `asyncio.Task`, поэтому HTTP handler не обязан ждать завершения переноса;
- использует отдельные SQLAlchemy sessions внутри worker-контекста и сохраняет каждое изменение статуса/прогресса в SQLite;
- ограничивает число одновременно исполняемых операций через `OPERATION_MAX_CONCURRENT_OPERATIONS`;
- создаёт отдельный private workspace `OPERATION_WORKSPACE_ROOT/operation-<id>` для каждого worker;
- перед стартом проверяет свободное место: учитывается глобальный `OPERATION_MIN_FREE_BYTES` и более высокий per-operation requirement, который смогут передавать orchestration-слои #17/#19;
- поддерживает пользовательскую отмену и штатную отмену при остановке backend;
- при старте backend детерминированно закрывает незавершённые non-resumable worker-стадии после аварийного/жёсткого рестарта.

## Интеграция с export/import

#16 не вводит отдельный публичный endpoint вида «запусти произвольную операцию»: без контрактов #17/#19 такой endpoint создавал бы фиктивную работу и дублировал бы orchestration API.

Export/import слой использует:

- `create_operation(...)` — создать persisted operation и artifact rows;
- `start(operation_id, worker, required_free_bytes=...)` — быстро запланировать существующую операцию;
- `create_and_start(...)` — helper «persist, затем schedule»;
- `OperationExecutionContext.transition(...)` — провести допустимый переход state machine #6;
- `set_progress(...)` — сохранить честный `current/total` без выдуманного ETA;
- `update_artifact(...)` — сохранить per-artifact result;
- `set_delivery_id(...)` — связать готовую delivery с операцией.

Worker обязан сам провести операцию в корректный terminal state. Если coroutine завершилась без `COMPLETED|FAILED|REJECTED|CANCELLED`, менеджер ставит `FAILED` с кодом `operation_worker_incomplete`.

## Отмена и subprocess

`POST /api/operations/{id}/cancel` разрешён `admin` и `operator`; operator может отменять только собственные операции. `viewer` остаётся read-only.

Если worker сейчас исполняется, менеджер вызывает `asyncio.Task.cancel()`. `CancelledError` проходит через ожидаемый service call. Skopeo/Helm runners уже обрабатывают `CancelledError`: дочерний процесс завершается, выполняется `wait()`, после чего исключение пробрасывается вверх. Поэтому отмена operation не оставляет штатно запущенный Skopeo/Helm subprocess без владельца.

Если операция находится в ожидающем состоянии без активного worker, например `UPLOADED`, `DISCOVERED` или `READY`, cancel выполняет обычный domain transition в `CANCELLED` без требования иметь task в памяти.

Стабильные причины отмены:

- `operation_cancelled_by_user` — явная пользовательская отмена;
- `operation_cancelled_shutdown` — штатное завершение backend отменило активный worker.

## Restart/recovery v1

Автоматическое возобновление worker с середины Skopeo/Helm/package шага в v1 **не реализуется**. После запуска backend менеджер переводит реально исполнявшиеся non-resumable состояния в:

- `status=FAILED`;
- `error_code=operation_interrupted_restart`;
- безопасное сообщение без subprocess output/credentials;
- `finished_at=<UTC restart recovery time>`.

К таким состояниям относятся `CREATED`, `VALIDATING`, `RUNNING`, `PACKAGING`, `VERIFYING`, `IMPORTING`, `VERIFYING_TARGET`.

`UPLOADED`, `DISCOVERED` и `READY` намеренно не считаются потерянным worker: это устойчивые import/waiting states, которые должны пережить restart и быть продолжены orchestration-слоем позднее.

Workspace прерванной исполнявшейся операции удаляется при recovery. История в БД сохраняется.

## Progress snapshot

`GET /api/operations/{id}` возвращает persisted данные, а не текст stdout/stderr:

- operation type/status, actor, delivery id и timestamps;
- безопасный error code/message;
- `progress.current` / `progress.total`;
- число завершённых/running/success/failed/skipped/conflict artifacts;
- `current_artifact_id`, если один из artifact rows имеет `RUNNING`;
- per-artifact status/digests/safe errors.

Raw subprocess logs через этот API не публикуются.

## Concurrency и workspace

Настройки:

- `OPERATION_WORKSPACE_ROOT=./data/tmp/operations`;
- `OPERATION_MAX_CONCURRENT_OPERATIONS=2`;
- `OPERATION_MIN_FREE_BYTES=0`.

`OPERATION_MIN_FREE_BYTES=0` означает отсутствие дополнительного глобального резерва. Это не отключает per-operation disk preflight: #17/#19 должны передавать реальную оценку требуемого места для конкретного export/import, когда она известна.

Повторный `start()` одного operation id при уже активном worker отклоняется с `operation_already_running`. Workspace разных operation id никогда не совпадает.

## Ограничения v1

- worker registry хранится в памяти одного backend-процесса;
- horizontal multi-process/multi-node execution без общей очереди не поддерживается;
- автоматического resume после hard restart нет;
- финальная fail-fast/continue-on-error политика для export/import определяется #17/#19, а не generic manager;
- audit/history UI и расширенная фильтрация относятся к #21.
