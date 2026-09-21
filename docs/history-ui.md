# History UI

Статус: **актуальный component document** для истории и lifecycle-действий операций.

## Назначение

`/history` показывает persisted состояние export/import операций из backend API. Terminal operations остаются историческими/read-only, а незавершённые операции можно открыть в исходном workflow или отменить через существующий backend lifecycle contract.

Источником product state является SQLite/backend API, а не текст container logs.

## Backend contract

Список загружается через `GET /api/operations` с bounded server-side pagination `limit/offset`. UI передаёт фильтры `type`, `status`, `actor`, `search`, `created_from`, `created_to`; порядок выдачи определяет backend.

Detail загружается через `GET /api/operations/{id}` и показывает безопасные persisted поля: actor, delivery id, timestamps, counters, bundle metadata, per-artifact status, SOURCE/TARGET digests и stable error code/message.

Для mapped TARGET import `artifact_results` дополнительно содержит immutable execution snapshot: SOURCE project/repository/reference, фактический TARGET project/repository/full reference, destination plan id/hash и explicit overwrite authorization state. History показывает SOURCE и TARGET отдельными колонками.

**Исторический TARGET никогда не вычисляется заново из текущих admin mapping defaults.** Изменение default image/Helm project, source→target map или policy revision после import не меняет уже сохранённую историю. Legacy rows без mapping snapshot остаются читаемыми: SOURCE использует существующие persisted identity fields, TARGET отображается как отсутствующий, а не угадывается.

## Receipt и bundle

Для terminal TARGET import экран пытается получить immutable receipt через существующий `GET /api/imports/{id}/receipt` только когда текущая роль соответствует backend policy. Если receipt недоступен роли или ещё не существует, сама история операции остаётся читаемой.

Receipt и History могут содержать разные представления одного immutable результата, но не должны расходиться по фактическому TARGET destination. Receipt опирается на сохранённый destination plan/outcome, а History — на persisted artifact outcome snapshot; current admin defaults не являются источником ни для одного исторического представления.

Для completed SOURCE export History показывает persisted bundle filename/size/SHA-256. Авторизованный download доступен только через существующий backend download-ticket contract и только если текущая роль/владелец соответствует policy. Отсутствие archive на диске не удаляет понятную history metadata.

## Фильтры и pagination

При применении фильтра offset сбрасывается в `0`. Следующая/предыдущая страницы запрашиваются у backend, а не режутся в браузере. Неверный локальный диапазон дат блокируется до HTTP request; backend повторно валидирует query независимо от frontend.

Поиск является общим backend `search` и используется, в частности, для delivery id/comment/error. Отдельные значения type/status/actor остаются структурированными фильтрами.

## Security и privacy

History UI:

- не отображает Authorization/JWT/Harbor credentials/private keys;
- не показывает raw stdout/stderr subprocess и не парсит logs как operation state;
- предоставляет только bounded lifecycle mutation для **незавершённых** operations: resume существующего workflow и cancel; terminal history не мутируется;
- не расширяет backend authorization: frontend role checks используются только для UX;
- показывает safe error fields из persisted operation/artifact state;
- сохраняет различие между bundle metadata и фактической доступностью package-файла;
- не подменяет отсутствующий persisted TARGET текущим policy/default mapping;
- не превращает `overwrite_approved` в утверждение, что overwrite фактически произошёл: это persisted authorization context конкретного execution.

## Состояния UX

Экран имеет отдельные loading, empty и safe error states. Detail открывается как keyboard-focusable button → dialog/panel и содержит per-artifact outcomes. Для mapped import detail явно показывает `SOURCE` и `TARGET`; длинный full TARGET reference допускает перенос по строкам без потери значения. Viewer получает read-only operation detail и не видит owner/admin/operator-owner lifecycle mutation.

Для незавершённой operation владелец или admin видит:

- **Продолжить/Открыть** — сохраняет существующий operation id в workspace pointer и возвращает в `/import` или `/export`; новая operation не создаётся;
- **Отменить** — вызывает существующий `POST /api/operations/{id}/cancel`, после чего запись остаётся в History со статусом `CANCELLED`.

`IMPORT READY` возвращается в verified preview/destination mapping того же operation id. Если runtime mode не совпадает с типом operation, UI не выполняет автоматический switch: современный mode switch отменяет незавершённые operations, поэтому History показывает объяснение вместо потенциально разрушительного implicit transition.

## Проверки

Frontend/backend regressions покрывают:

- projection filters/pagination в backend query;
- сброс offset при новом фильтре;
- переход между server-side страницами;
- terminal import receipt по разрешённой role policy;
- viewer read-only behavior;
- resume existing READY/import and active export/import workflow without duplicate operation;
- cancel unfinished operation from History and immediate CANCELLED projection;
- runtime-mode mismatch explanation without implicit switch;
- invalid local date range;
- operation detail и per-artifact outcomes;
- mapped SOURCE/TARGET references из persisted snapshot;
- legacy artifact row без mapping snapshot;
- empty/error states.

Обязательный merge gate: backend lint/type/unit/API tests для persistence/API изменений, frontend lint/TypeScript/Vitest/build для UI projection, relevant integration/acceptance gates, documentation links и общий `quality-gate`.
