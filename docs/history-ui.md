# History UI

Статус: **актуальный component document** для read-only экрана истории операций.

## Назначение

`/history` показывает persisted состояние export/import операций из backend API. Экран предназначен для `viewer`, `operator` и `admin` и не является интерфейсом управления операциями: из History нельзя запускать, отменять, перезапускать или менять policy операции.

Источником product state является SQLite/backend API, а не текст container logs.

## Backend contract

Список загружается через `GET /api/operations` с bounded server-side pagination `limit/offset`. UI передаёт фильтры `type`, `status`, `actor`, `search`, `created_from`, `created_to`; порядок выдачи определяет backend.

Detail загружается через `GET /api/operations/{id}` и показывает безопасные persisted поля: actor, delivery id, timestamps, counters, bundle metadata, per-artifact status, SOURCE/TARGET digests и stable error code/message.

## Receipt и bundle

Для terminal TARGET import экран пытается получить immutable receipt через существующий `GET /api/imports/{id}/receipt` только когда текущая роль соответствует backend policy. Если receipt недоступен роли или ещё не существует, сама история операции остаётся читаемой.

Для completed SOURCE export History показывает persisted bundle filename/size/SHA-256. Авторизованный download доступен только через существующий backend download-ticket contract и только если текущая роль/владелец соответствует policy. Отсутствие archive на диске не удаляет понятную history metadata.

## Фильтры и pagination

При применении фильтра offset сбрасывается в `0`. Следующая/предыдущая страницы запрашиваются у backend, а не режутся в браузере. Неверный локальный диапазон дат блокируется до HTTP request; backend повторно валидирует query независимо от frontend.

Поиск является общим backend `search` и используется, в частности, для delivery id/comment/error. Отдельные значения type/status/actor остаются структурированными фильтрами.

## Security и privacy

History UI:

- не отображает Authorization/JWT/Harbor credentials/private keys;
- не показывает raw stdout/stderr subprocess и не парсит logs как operation state;
- не предоставляет mutation controls;
- не расширяет backend authorization: frontend role checks используются только для UX;
- показывает safe error fields из persisted operation/artifact state;
- сохраняет различие между bundle metadata и фактической доступностью package-файла.

## Состояния UX

Экран имеет отдельные loading, empty и safe error states. Detail открывается как keyboard-focusable button → dialog/panel и содержит per-artifact outcomes. Viewer получает тот же read-only operation detail, но не видит owner/admin-only download action и не инициирует restricted receipt request.

## Проверки

Frontend regressions покрывают:

- projection filters/pagination в backend query;
- сброс offset при новом фильтре;
- переход между server-side страницами;
- terminal import receipt по разрешённой role policy;
- viewer read-only behavior;
- invalid local date range;
- operation detail и per-artifact outcomes;
- empty/error states.

Обязательный merge gate: frontend lint, TypeScript typecheck, Vitest, production build, documentation links и общий `quality-gate`.
