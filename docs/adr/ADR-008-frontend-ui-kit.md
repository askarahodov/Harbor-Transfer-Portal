# ADR-008: UI-kit для frontend

## Статус

Принято.

## Контекст

Harbor Transfer Portal нужны единообразные формы, таблицы, шаги мастеров, диалоги и состояния обратной связи для сценариев экспорта и импорта. UI должен полностью работать в изолированном контуре без runtime-зависимости от CDN или внешних шрифтов.

Спецификация допускает Element Plus или Naive UI.

## Рассмотренные варианты

- Element Plus;
- Naive UI;
- собственная компонентная библиотека с нуля.

## Решение

Использовать **Element Plus** как основной универсальный UI-kit и Lucide для иконок.

Проектные примитивы — badge контура/статуса, application shell и empty/loading/error states — остаются небольшими token-driven компонентами. Не требуется оборачивать каждый компонент Element Plus в собственную абстракцию.

Ответ локального backend `GET /api/health` является основным runtime-источником значения `SOURCE`/`TARGET`. `public/runtime-config.js` может предварительно задать то же значение при запуске контейнера как offline-safe fallback до завершения backend bootstrap. Страницы не должны hardcode значение контура.

## Причины

- зрелая поддержка Vue 3 и TypeScript;
- готовые компоненты для таблиц, форм, steps и dialogs, нужных будущим мастерам;
- все зависимости попадают в локальную frontend-сборку и не требуют CDN в runtime;
- уменьшается объём собственного UI-кода при сохранении визуальной идентичности через project tokens;
- переиспользуется существующий backend health contract вместо отдельного frontend-specific configuration API.

## Последствия

- Element Plus увеличивает frontend bundle; оптимизация импортов выполняется только после измерения bundle size;
- Google Fonts и внешние CDN-ресурсы в runtime запрещены; foundation использует системный font stack;
- deployment issue #5 должен формировать или сохранять `runtime-config.js`, не помещая в него credentials или другую секретную конфигурацию.
