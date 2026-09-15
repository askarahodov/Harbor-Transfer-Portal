# Deterministic retry для partial TARGET import

Статус: **нормативное дополнение** к [TARGET import orchestration](import-orchestration.md) и [destination plan integrity](destination-plan-integrity.md).

Документ описывает штатный retry после `FAILED/import_partial_failure`. Формат Offline Bundle v1 не меняется.

## Базовый принцип

Harbor import не является общей транзакцией для нескольких OCI artifacts. Если image уже импортирован, а следующий Helm chart завершился ошибкой, Portal не обещает и не имитирует rollback уже выполненной Harbor mutation.

Исходная terminal operation остаётся immutable history entry с фактическими per-artifact результатами. Retry всегда создаёт **новую import operation** и сохраняет ссылку на исходную operation.

## Что фиксируется при retry

Новая retry operation наследует только immutable identity исходной попытки:

- тот же staged physical bundle;
- exact bundle SHA256 и SOURCE delivery id;
- исходный persisted destination `plan_id`/`plan_hash`;
- frozen effective mapping, использованный исходным plan;
- `retry.of_operation_id`;
- actor новой попытки;
- failure policy `continue-on-error`.

Текущие global destination mapping defaults при retry не применяются. После подготовки retry destination mapping становится immutable. Если оператору нужен другой mapping, он должен создать новый Preview/destination plan и отдельную import operation, а не переписывать retry plan.

Overwrite approval из исходной operation не переносится. Retry начинается с `overwrite=false` и новый TARGET conflict остаётся default-deny.

## Revalidation

До создания Harbor mutation Portal заново проверяет каждый final TARGET reference.

| Current TARGET state | Retry behavior |
|---|---|
| `SAME` | `SKIPPED`, повторной registry mutation нет |
| `NEW` | artifact может быть обработан повторно |
| `CONFLICT` | retry execute блокируется по умолчанию |
| `UNKNOWN` / `ERROR` | fail-closed, execute не разрешается |

Это правило одинаково применяется к artifacts, которые в исходной попытке были успешными, failed или ещё не были обработаны. Поэтому failed artifact, который после сбоя уже появился в TARGET с ожидаемой identity, безопасно становится `SKIPPED`.

Перед фактической mutation worker повторяет TARGET inspection ещё раз. Preview/retry plan не считается достаточным доказательством неизменности Harbor между revalidation и execute.

## Restart и persisted state

Retry не зависит от in-memory state предыдущего процесса. После restart Portal восстанавливает необходимые данные из SQLite и staging:

- исходная operation и immutable artifact mapping snapshot;
- retry ancestry;
- bundle path metadata и SHA256;
- destination plan;
- actor и policy.

Если staged bundle отсутствует, path metadata повреждена или persisted artifact snapshot не совпадает с destination plan, retry запрещается до создания новой Harbor mutation.

## UI

Действие `Retry` доступно operator/admin только для `IMPORT/FAILED` с `error_code=import_partial_failure` и только в TARGET workspace.

После нажатия Portal сначала показывает новую revalidation:

- `SAME` — уже присутствует и будет skipped;
- `NEW` — будет повторно обработан;
- `CONFLICT` — TARGET изменился, execute заблокирован;
- `UNKNOWN/ERROR` — состояние нельзя безопасно разрешить, execute заблокирован.

Viewer видит history, но не получает Retry action. UI не сообщает о rollback, потому что rollback уже успешно импортированных Harbor artifacts не выполняется.

## History, receipt и report

Исходная и retry operations никогда не объединяются в одну запись.

History API для retry operation содержит `retry_of_operation_id` и `failure_policy`. Immutable import receipt содержит те же lineage fields вместе с destination plan и фактическими per-artifact outcomes.

CSV/PDF operation reports сохраняют собственный исторический результат каждой operation. Retry report содержит operation comment вида `Retry of import #<id>; failure policy: continue-on-error`, поэтому связь с исходной попыткой остаётся видимой и не переписывает первый report.

## API

Подготовка retry:

`POST /api/imports/{operation_id}/retry`

Request:

```json
{
  "destination_plan_id": "<original stable plan id>"
}
```

Backend отклоняет retry, если operation не является partial failure, plan id отличается от исходного, bundle/staging отсутствует либо persisted snapshot повреждён.

После успешной подготовки возвращается новая READY operation и fresh revalidated destination plan. Execute выполняется обычным import endpoint с plan id новой retry operation и без автоматического overwrite.

## Failure policy

Текущий v1 behavior — `continue-on-error`: независимые artifacts продолжают обрабатываться после ошибки одного artifact. Итоговые состояния каждого artifact сохраняются точно. Если есть failures, operation завершается `FAILED/import_partial_failure`; успешно выполненные artifacts не откатываются.

## Проверяемые инварианты

Regression suite обязана подтверждать:

- partial result не мутируется при retry;
- frozen mapping сохраняется при изменившихся global defaults;
- другой plan id отклоняется;
- retry destination mapping нельзя заменить после подготовки;
- успешно импортированный artifact, который остаётся `SAME`, не вызывает duplicate mutation;
- failed artifact, ставший `SAME`, безопасно получает `SKIPPED`;
- failed/NEW artifact повторно обрабатывается после fresh pre-mutation inspection;
- TARGET drift в `CONFLICT` блокирует retry без implicit overwrite;
- retry после restart работает только из persisted state;
- receipt/history/report различают исходную и retry operations;
- Bundle v1 остаётся без изменений.
