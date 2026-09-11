# ADR-004: Аутентификация, авторизация и frontend-сессия

- **Статус:** принято
- **Дата:** 2026-09-11

## Контекст

Backend Harbor Transfer Portal уже использует локальных пользователей, роли `admin`, `operator`, `viewer` и короткоживущий JWT bearer token. Endpoint `POST /api/auth/login` возвращает access token, а `GET /api/auth/me` подтверждает, что пользователь существует, активен и по-прежнему имеет указанную роль.

Задаче P2.3 нужен browser session UX, который работает полностью офлайн, переживает обычное обновление страницы и не превращает скрытие элементов интерфейса в замену серверной авторизации.

## Рассмотренные варианты

### HttpOnly Secure cookie

Предпочтителен с точки зрения недоступности токена JavaScript-коду, но текущий backend-контракт не устанавливает cookie и не реализует cookie/CSRF lifecycle. Вводить второй параллельный auth-механизм только во frontend PR означало бы изменить backend security contract вне scope задачи.

### `localStorage`

Переживает закрытие браузера, но тем самым увеличивает срок нахождения bearer token в доступном JavaScript storage и создаёт нежелательную долговременную browser-сессию для операторского портала.

### `sessionStorage`

Совместим с текущим bearer API, переживает reload в той же вкладке и автоматически исчезает после завершения browser session. Как и любой JavaScript-readable storage, не защищает token от успешной XSS-атаки, поэтому требует строгой XSS-гигиены и не считается эквивалентом HttpOnly cookie.

## Решение

1. Access token хранится только в `sessionStorage` текущей browser session. Пароль не сохраняется нигде во frontend state/storage.
2. Axios добавляет `Authorization: Bearer …` только при наличии token.
3. При bootstrap наличие token само по себе не означает аутентифицированную сессию: frontend вызывает `GET /api/auth/me` и строит user/role state только из успешного ответа backend.
4. Любой authenticated API response `401` удаляет token и текущего пользователя. Автоматический retry с тем же token запрещён, чтобы не создавать redirect/retry loop.
5. `403` не очищает сессию: это отказ в конкретном действии, а не признак истёкшей аутентификации.
6. Route guards защищают все private routes. `/export` и `/import` допускают `operator|admin`, `/settings` — только `admin`. Viewer остаётся read-only.
7. Navigation скрывает недоступные действия для UX, но backend RBAC остаётся окончательным security control.
8. Logout очищает локальный token/state. Серверного logout/revocation endpoint сейчас нет; существующий JWT ограничен сроком жизни, а backend дополнительно отвергает token отключённого пользователя.

## Последствия и риски

- Bearer header не отправляется браузером автоматически как cookie, поэтому выбранная схема не создаёт cookie-based CSRF surface для API mutations.
- Успешная XSS-атака в рамках активной вкладки потенциально может прочитать `sessionStorage`. Поэтому нельзя добавлять небезопасный `v-html`, runtime CDN/script injection или другие источники исполняемого недоверенного контента; CSP следует усиливать на deployment-слое по мере стабилизации Nginx runtime.
- Переход на HttpOnly cookie в будущем потребует согласованного backend ADR/контракта и CSRF-защиты для state-changing requests; он не должен быть выполнен скрыто только на стороне UI.

## Проверка

Обязательные frontend tests покрывают:

- session bootstrap через `/auth/me`;
- bearer header и очистку session на `401` без retry;
- generic invalid-credentials UX;
- route guard для unauthenticated пользователя;
- role matrix viewer/operator/admin для navigation и routes;
- password visibility control и базовую keyboard semantics формы.
