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
Docsify routes. Конфигурация использует `loadSidebar: true`, чтобы Docsify выполнял
штатный sidebar discovery для текущего route. Alias перенаправляет root/nested
`_sidebar.md` requests на единый `/docs/_sidebar.md`, поэтому меню не исчезает при
переходе к repository-level или deployment pages.

В sidebar используются **обычные Markdown links на site-root paths**, например
`/docs/dashboard.md`, а не вручную собранные hash links вида `#/docs/dashboard`.
Docsify сам преобразует Markdown target в hash route. Это важно для вложенного deployment
под `/docs/`: одновременно использовать `relativePath: true` и вручную кодировать
Docsify hash route нельзя, иначе route resolution может уйти в неверный Markdown path.

Repository-level страницы (`README`, `CONTRIBUTING`, deployment guides) также
указываются как реальные Markdown paths, поэтому sidebar не зависит от текущей вложенности.

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

Nginx имеет отдельную boundary для `/docs/`: существующие Markdown/assets отдаются как
static files, а отсутствующий docs path возвращает настоящий `404` и не проваливается в
Vue SPA fallback. Благодаря этому ошибочная ссылка не превращается в визуально пустую
Docsify-страницу, получившую `frontend/index.html` вместо Markdown. Для 404 Docsify
показывает локальную [_404.md](_404.md).

## Визуальная оболочка

Documentation shell следует дизайн-системе Portal:

- верхняя панель показывает Harbor Transfer Portal, текущий документ и кнопку
  **Назад в портал**;
- sidebar использует тот же brand surface и active/hover pattern, что основная
  навигация Portal;
- sidebar использует high-contrast token-based hierarchy: group headings и active item
  используют `--color-on-accent`, обычные links — `--color-brand-text-muted`; vendor
  theme не должен делать неактивные пункты похожими на disabled state;
- на desktop article + right TOC образуют единый центрированный CSS Grid workspace:
  article ограничен readable шириной `980px`, TOC — `240px`, между ними используется
  design-system spacing;
- right TOC переносится внутрь Docsify `.content` после render и становится sticky
  второй колонкой, поэтому он не центрируется отдельно от статьи относительно viewport;
- при ширине viewport до `1400px` secondary TOC скрывается, а article автоматически
  становится единственной центрированной колонкой;
- основная область использует один continuous surface background; статья не создаёт
  второй вложенный фон/карточку;
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

| Раздел Portal | Документ / блок |
|---|---|
| Вход | [user-guide.md](user-guide.md) · `id=login` |
| Главная | [dashboard.md](dashboard.md) |
| Отправка | [user-guide.md](user-guide.md) · `id=source-export` |
| Приём | [user-guide.md](user-guide.md) · `id=target-import` |
| История | [history-ui.md](history-ui.md) |
| Пользователи | [admin-user-management.md](admin-user-management.md) |
| Настройки | [settings.md](settings.md) |

Authenticated layout показывает кнопку **Документация** в верхней панели. Login имеет
отдельную ссылку, поэтому помощь доступна до аутентификации.

Для длинного общего guide route metadata использует стабильный explicit Docsify heading ID,
например `/docs/user-guide?id=source-export`. Такой anchor объявляется непосредственно в
Markdown через `:id=...`; regression test проверяет, что target file и anchor существуют.

## Как добавлять новую документацию

1. Добавьте или обновите Markdown в `docs/` либо соответствующий repository-level guide.
2. Добавьте страницу в [_sidebar.md](_sidebar.md), если она должна быть видна в общей навигации.
3. Для нового UI route задайте `meta.documentation` в frontend router. Если route ведёт на блок длинного документа, сначала объявите стабильный `:id=...` в Markdown и используйте `?id=<anchor>` в target.
4. Обновите documentation impact в той же итерации, что и код.
5. В sidebar добавляйте Markdown target, а не hash route. Для файла в `docs/` используйте
   site-root форму `/docs/<name>.md`.
6. Если меняется navigation shell, anchors или packaging, обновите regression checks в
   `tools/test_frontend_system_ui.py`, docs link checker и Compose smoke.
7. Запустите `make docs-check` и scoped frontend/Compose tests.

Docsify не меняет правило source-of-truth: если rendered site и Markdown расходятся,
дефект находится в packaging/navigation, а не решается копированием текста в отдельное
хранилище.
