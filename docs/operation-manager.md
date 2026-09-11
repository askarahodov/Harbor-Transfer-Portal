# Менеджер фоновых операций

## Назначение

`OperationManager` выполняет длительные export/import-задачи вне жизненного цикла HTTP-запроса и сохраняет наблюдаемое состояние в SQLite. Для baseline v1 используется один backend instance и стандартный `asyncio`; Redis, Celery и отдельный broker не требуются.

Финальная логика экспорта и импорта остаётся в задачах #17 и #19. Они создают `Operation`, добавляют `ArtifactResult` и передают асинхронный worker в `OperationManager`.

## Жизненный цикл worker

Перед запуском manager атомарно захватывает операцию через persistent `worker_token`. Захват разрешён только если операция не терминальная, не имеет другого worker и для неё не запрошена отмена. Поэтому две coroutine или два случайно запущенных manager в одной SQLite-базе не должны одновременно выполнять одну операцию.

Worker получает `OperationContext` и через него:

- переводит operation по state machine из Bundle Protocol v1;
- сохраняет структурный progress;
- обновляет per-artifact status/digest/error;
- проверяет cancellation request;
- получает изолированный workspace;
- выполняет disk-space preflight.

Каждое такое обновление использует отдельную короткую DB-session. Нельзя передавать SQLAlchemy entity/session из HTTP request в background task.

## Progress API

`GET /api/operations/{id}` доступен аутентифицированным ролям и возвращает persisted state, а не текст логов:

- текущую phase/status;
- total/completed/running/failed/skipped/conflict artifact counts;
- `progress_current`/`progress_total` только для реально известного progress;
- running artifact ids;
- безопасные operation/artifact error code/message.

API намеренно не придумывает ETA или процент выполнения и не возвращает `worker_token` или subprocess output.

## Отмена

`POST /api/operations/{id}/cancel` разрешён:

- `admin` — для любой операции;
- `operator` — для собственной операции;
- `viewer` — никогда.

Сначала сохраняется `cancel_requested_at`, затем локальная `asyncio.Task` получает `cancel()`. Если worker в этот момент ожидает Skopeo/Helm service, `asyncio.CancelledError` проходит в существующие subprocess runners, которые завершают дочерний процесс и дожидаются его остановки. После отмены операция переходит в `CANCELLED`, а активный artifact — в `FAILED` с безопасным кодом отмены.

Worker, выполняющий длинную чисто Python-стадию между await points, должен периодически вызывать `context.raise_if_cancelled()`.

## Restart и recovery

Resume середины Skopeo/Helm-команды в v1 **не поддерживается**. При старте backend manager выполняет reconciliation.

Состояния активного исполнения:

`VALIDATING`, `RUNNING`, `PACKAGING`, `VERIFYING`, `IMPORTING`, `VERIFYING_TARGET`.

Если backend был остановлен/потерян в одном из этих состояний, операция переводится в `FAILED` с кодом `operation_interrupted_restart`; активные `RUNNING` artifacts также становятся `FAILED`, временный workspace удаляется. Такая операция никогда не считается успешно завершённой после рестарта.

Persistent `worker_token` означает, что конкретная операция уже была передана worker manager. Поэтому захваченная операция, которая ещё не успела перейти из `CREATED`, `UPLOADED` или `DISCOVERED` в следующую фазу и была прервана restart, также считается non-resumable и переводится в `FAILED` с `operation_interrupted_restart`. Это предотвращает появление операций без worker, которые навсегда остаются в нетерминальном состоянии.

`READY` — единственное ожидающее состояние, stale worker claim которого освобождается без failure. Workspace `READY` сохраняется, потому что он может содержать уже проверенный intake, необходимый будущему import orchestration.

При штатной остановке backend все локальные tasks отменяются. Любая уже захваченная non-terminal операция, кроме `READY`, завершается `FAILED` с `operation_interrupted_shutdown`, даже если task ещё ожидал свободный semaphore slot и не выполнил первый state transition. Для `READY` ownership освобождается, а подготовленный workspace сохраняется.

## Concurrency, workspace и диск

Настройки:

- `OPERATION_WORKSPACE_ROOT` — корень private workspace;
- `OPERATION_MAX_CONCURRENT` — максимальное число одновременно исполняемых операций;
- `OPERATION_DISK_RESERVE_BYTES` — минимальный свободный reserve поверх оценённой потребности;
- `OPERATION_SHUTDOWN_TIMEOUT_SECONDS` — bounded wait при graceful shutdown.

Workspace имеет server-generated имя `operation-<id>` и mode `0700`; symlink вместо workspace отклоняется. Оркестратор #17/#19 может дополнительно вызвать `context.require_disk(expected_bytes)` перед крупной выгрузкой.

## Ошибки и безопасность

Ожидаемая ошибка worker должна использовать `OperationTaskFailure(code, safe_message)`. Необработанное исключение журналируется server-side, но в БД сохраняется только `operation_worker_failed` и общий безопасный текст. Raw exception, Harbor credentials, JWT, private key и полный subprocess output не должны становиться operation error payload.

После terminal failure/cancel временный workspace очищается. `READY` import workspace является исключением до запуска/отмены дальнейшего import workflow.

## Контракт для #17 и #19

Будущие orchestration services должны:

1. создать operation и artifact rows через `create_operation()`/`create_and_submit()`;
2. немедленно вернуть operation id HTTP-клиенту;
3. выполнять работу только внутри async worker;
4. пользоваться `OperationContext` для state/progress/artifact updates;
5. не публиковать успешный export/import до соответствующей verification phase;
6. на ожидаемых ошибках выбрасывать `OperationTaskFailure` с стабильным безопасным кодом;
7. между длительными локальными стадиями проверять cancellation;
8. не передавать request-scoped DB-session в worker.
