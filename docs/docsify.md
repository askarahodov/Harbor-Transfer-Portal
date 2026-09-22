# Локальный documentation portal (Docsify)

## Назначение

`docs/` остаётся source of truth для человекоориентированной документации, а Docsify
отображает эти Markdown-файлы как локальный web-site. Отдельная база, генерация копий
документов или внешний documentation server не используются.

Docsify включён непосредственно во frontend image. После запуска Portal документация
доступна на том же опубликованном frontend host/port по пути `/docs/`.

## Адрес

При стандартных настройках:

```text
Portal:        http://127.0.0.1:8080/
Documentation: http://127.0.0.1:8080/docs/
```

Если изменён `PORTAL_HTTP_BIND` или `PORTAL_HTTP_PORT`, используется тот же host/port,
что и для Portal, с добавлением `/docs/`.

## Запуск на Linux

После подготовки `.env`:

```bash
docker compose up -d --build --force-recreate
```

Откройте `http://127.0.0.1:8080/docs/`.

Canonical developer workflow также описан в [development.md](development.md).

## Запуск на Windows через CMD

PowerShell для просмотра документации не требуется. В каталоге репозитория после
подготовки `.env`:

```bat
docker compose up -d --build --force-recreate
start "" http://127.0.0.1:8080/docs/
```

Docker Desktop должен работать в Linux containers mode, как описано в
[development.md](development.md).

## Offline boundary

Во время Docker build используется фиксированная версия Docsify. В final frontend image
копируются Docsify runtime, search plugin, theme и Markdown sources. После сборки image
браузер не загружает JavaScript/CSS с CDN и не требует Internet.

Offline install kit сохраняет это свойство, потому что поставляет уже собранный frontend
image. Отдельный Docsify container или дополнительный published port не нужен.

## Навигация

Sidebar определён в [_sidebar.md](_sidebar.md) и является общей навигацией для всех
Docsify routes. Конфигурация `loadSidebar: true` использует единый sidebar также для
repository-level страниц (`README`, `CONTRIBUTING`, deployment guides), поэтому переход
между каталогами не должен приводить к исчезновению меню.

Навигация группирует:

- пользовательскую и административную документацию;
- SOURCE/TARGET transfer flow;
- Harbor/runtime;
- эксплуатацию и безопасность;
- архитектуру и ADR;
- repository-level README/CONTRIBUTING/CHANGELOG;
- исторический reference.

На desktop справа отображается блок **На этой странице** по заголовкам `H2/H3`.
У каждого такого заголовка есть заметная permalink-ссылка. Anchor использует
Docsify-compatible URL вида `#/docs/dashboard?id=проверки`, поэтому ссылка сохраняет
текущий документ и ведёт на конкретный блок.

Search plugin индексирует открываемые Markdown pages в браузере.

## Визуальная оболочка

Documentation shell следует дизайн-системе Portal:

- верхняя панель показывает Harbor Transfer Portal, текущий документ и кнопку
  **Назад в портал**;
- sidebar использует тот же brand surface и active/hover pattern, что основная
  навигация Portal;
- статья, таблицы, code blocks, focus ring и responsive breakpoints используют
  semantic design tokens frontend;
- `frontend/src/styles/tokens.css` остаётся источником истины и при Docker build
  копируется в `/docs/_portal/tokens.css`; отдельная палитра документации не
  поддерживается;
- на узких экранах правый TOC скрывается, а Docsify sidebar остаётся доступным через
  штатный mobile toggle.

Кастомные стили находятся в [portal-docs.css](portal-docs.css).

## Контекстные ссылки из Portal

Route metadata во frontend задаёт documentation target для основных экранов:

| Раздел Portal | Документ |
|---|---|
| Вход | [user-guide.md](user-guide.md) |
| Главная | [dashboard.md](dashboard.md) |
| Отправка | [user-guide.md](user-guide.md) |
| Приём | [user-guide.md](user-guide.md) |
| История | [history-ui.md](history-ui.md) |
| Пользователи | [admin-user-management.md](admin-user-management.md) |
| Настройки | [admin-guide.md](admin-guide.md) |

Authenticated layout показывает кнопку **Документация** в верхней панели. Login имеет
отдельную ссылку, поэтому помощь доступна до аутентификации.

## Как добавлять новую документацию

1. Добавьте или обновите Markdown в `docs/` либо соответствующий repository-level guide.
2. Добавьте страницу в [_sidebar.md](_sidebar.md), если она должна быть видна в общей навигации.
3. Для нового UI route задайте `meta.documentation` в frontend router.
4. Обновите documentation impact в той же итерации, что и код.
5. Если меняется navigation shell, anchors или packaging, обновите regression checks в
   `tools/test_frontend_system_ui.py` и Compose smoke.
6. Запустите `make docs-check` и scoped frontend/Compose tests.

Docsify не меняет правило source-of-truth: если rendered site и Markdown расходятся,
дефект находится в packaging/navigation, а не решается копированием текста в отдельное
хранилище.
