# Frontend

## Стек

- Vue 3 + Vite + TypeScript
- Vue Router, Pinia (state management)
- Axios (HTTP-клиент)
- Element Plus (UI-кит)
- Lucide (иконки)
- Inter / JetBrains Mono (типографика)

## Маршруты

| Путь | Имя | Компонент | Публичный |
|---|---|---|---|
| `/login` | login | `LoginView.vue` | да |
| `/` | dashboard | `DashboardView.vue` | нет |
| `/export` | export | `ExportView.vue` | нет |
| `/import` | import | `ImportView.vue` | нет |
| `/history` | history | `HistoryView.vue` | нет |
| `/settings` | settings | `SettingsView.vue` | нет |

## Определение контура

Основным источником контура является локальный `GET /api/health` (отвечает с `contour: SOURCE|TARGET`). `frontend/src/public/runtime-config.js` используется только как offline-safe bootstrap fallback.

Контур не hardcode в страницах: компонент `ContourBadge` отображает значение из store, обновляемое при монтировании приложения.

## Дизайн-токены

Цвета, радиусы, отступы и типографика описаны в `frontend/src/styles/tokens.css`. Подробное описание палитры, иконок и анимаций — в `docs/harbor-transfer-portal.md`, раздел 6.

## Структура

```
frontend/
├── src/
│   ├── api/client.ts          # Axios instance + interceptors
│   ├── App.vue
│   ├── main.ts
│   ├── router/index.ts
│   ├── stores/runtime.ts      # contour, auth, settings
│   ├── components/
│   │   ├── ContourBadge.vue
│   │   ├── PageShell.vue
│   │   ├── StatePlaceholder.vue
│   │   └── StatusBadge.vue
│   ├── styles/
│   │   ├── base.css
│   │   └── tokens.css
│   └── views/
│       ├── DashboardView.vue
│       ├── ExportView.vue
│       ├── HistoryView.vue
│       ├── ImportView.vue
│       ├── LoginView.vue
│       └── SettingsView.vue
└── public/runtime-config.js
```

## Локальная разработка

```bash
cd frontend
npm install
npm run dev
```

Проверки:

```bash
npm run lint
npm run typecheck
npm test
npm run build
```