# Dashboard — контурная главная страница

Статус: **актуальный component document** для `/` после аутентификации.

## Назначение

Dashboard даёт оператору безопасную точку входа в работу портала и всегда показывает, в каком изолированном контуре открыт текущий экземпляр. Экран не создаёт отдельную backend-модель и использует уже существующие источники состояния.

## Источники данных

Dashboard использует:

- текущего пользователя из `GET /api/auth/me`, загружаемого auth store;
- контур из `GET /api/health` с `runtime-config.js` только как bootstrap fallback;
- состояние локального Harbor из `GET /api/harbor/connection`;
- последние пять persisted операций из `GET /api/operations?limit=5&offset=0`.

История операций остаётся source of truth для product state. Dashboard не парсит container logs и не подменяет History API локальной browser-фильтрацией.

## Контур и основное действие

Контур всегда показан текстом, а не только цветом.

Для `SOURCE` основное действие — **«Отправить артефакты»** с переходом в `/export`. Для `TARGET` основное действие — **«Принять пакет»** с переходом в `/import`.

Если backend не подтвердил контур, кнопка запуска transfer workflow не показывается. Пользователь получает явное предупреждение, чтобы не начинать перенос, пока SOURCE/TARGET не определён достоверно.

## Роли

`admin` и `operator` видят разрешённое для текущего контура основное действие. `viewer` получает read-only Dashboard: состояние Harbor, последние операции и переход в полную историю доступны, но кнопки запуска export/import не отображаются.

Frontend role/contour checks являются UX-ограничением и не заменяют backend RBAC/contour authorization.

## Harbor status

Harbor card показывает только безопасные данные: факт подключения, версию и режим аутентификации, если backend их сообщил. Credentials, CA contents и другие секреты на Dashboard не отображаются.

Ошибка Harbor не скрывает persisted историю операций. Для `admin` UI предлагает проверить URL, credentials и TLS/CA в настройках; для других ролей — обратиться к администратору. Проверку можно повторить отдельно, не перезагружая историю.

## Последние операции

Dashboard показывает не более пяти последних операций в deterministic ordering backend history API. Для каждой строки отображаются:

- тип export/import;
- delivery id или operation id;
- actor;
- persisted status;
- дата создания;
- счётчики успешных артефактов, ошибок и конфликтов, когда они есть.

Полная фильтрация и детали остаются на `/history`.

## Empty/error состояния

Если операций ещё нет, operator/admin получает подсказку о следующем допустимом действии для текущего контура. Viewer получает read-only объяснение. Ошибки Harbor и history независимы: отказ одного источника не должен скрывать исправные данные другого.

## Accessibility и responsive behavior

SOURCE/TARGET и статусы имеют текстовые подписи и не кодируются одним цветом. Интерактивные действия реализованы как ссылки или кнопки с keyboard focus. На узких экранах карточки и строки операций переходят в одноколоночный layout без потери основных действий и статусов.

## Проверки

Frontend regressions покрывают:

- SOURCE → `/export` и TARGET → `/import`;
- отсутствие transfer action у `viewer`;
- реальные recent operations и переход `/history`;
- Harbor failure при сохранении history content;
- пустую историю и корректный next-action hint;
- dashboard store query `limit=5&offset=0`;
- независимые ошибки Harbor/history.

Merge gate: ESLint, TypeScript, Vitest, production build, проверка документации и общий `quality-gate`.
