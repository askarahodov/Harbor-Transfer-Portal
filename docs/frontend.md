# Frontend

**Статус:** актуальное описание frontend текущей разработки v1.

## Стек

- Vue 3 + Vite + TypeScript;
- Vue Router;
- Pinia для application state;
- Axios для JSON/API requests;
- Element Plus как UI dependency;
- Lucide для иконок;
- CSS design tokens из `frontend/src/styles/tokens.css`.

## Маршруты и доступ

| Путь | Компонент | Доступ | Контур |
|---|---|---|---|
| `/login` | `LoginView.vue` | публичный | оба |
| `/` | `DashboardView.vue` | authenticated | оба |
| `/export` | `ExportView.vue` | `admin`, `operator` | только `SOURCE` |
| `/import` | `ImportView.vue` | `admin`, `operator` | только `TARGET` |
| `/history` | `HistoryView.vue` | authenticated | оба |
| `/settings` | `SettingsView.vue` | `admin` | оба |

Role/contour restrictions реализованы не только визуально. Vue Router guard проверяет роль и runtime contour; backend отдельно применяет собственный RBAC/contour policy и остаётся authoritative security boundary.

Sidebar также contour-aware: SOURCE operator/admin видит «Отправка», TARGET operator/admin — «Приём». Пока contour не определён, frontend не угадывает transfer route.

## Определение контура

Основным источником контура является локальный `GET /api/health` с `contour: SOURCE|TARGET`. `frontend/public/runtime-config.js` используется только как offline-safe bootstrap fallback.

`runtime` Pinia store загружает contour и используется навигацией, route guards и transfer views. Страница не должна разрешать SOURCE/TARGET workflow только на основании URL route.

## SOURCE export wizard

`ExportView.vue` реализует пользовательский flow задачи #18 поверх backend orchestration #17. State machine находится в `stores/exportWizard.ts`, typed API contract — в `api/exports.ts`.

### Шаг 1 — выбор

Wizard работает только с реальными Harbor browse endpoints:

1. выбирается project;
2. выбирается repository;
3. показываются artifacts и их явные references;
4. operator выбирает конкретный image tag/digest либо Helm version/tag.

Selection хранит `kind`, `project`, `repository`, `reference` и pinned `digest`. Выбор не теряется при search/pagination. `unknown-oci` и Helm artifact без явной версии выбрать нельзя. UI показывает известный размер и отдельно отмечает artifacts с неизвестным размером.

### Шаг 2 — preview

Frontend вызывает `POST /api/exports/preview` и показывает только подтверждённые backend значения: exact reference, authoritative SOURCE digest и размер. Отображаются SOURCE contour, состояние локального Harbor, оценка payload и operator comment с лимитом 2000 символов.

Preview не заменяет worker validation: backend повторно проверяет SOURCE после запуска операции.

### Шаг 3 — progress

После `POST /api/exports` wizard сохраняет `operation_id` в `sessionStorage` под ключом `htp.export.operation-id` и использует `GET /api/operations/{id}`.

Показываются:

- текущая persisted phase;
- completed/total artifacts;
- coarse progress только из backend counters;
- per-artifact state/error;
- elapsed time;
- cancel action только для active export state.

ETA не выдумывается. При reload/reconnect сохранённый operation id открывается снова; terminal `COMPLETED` приводит к загрузке bundle metadata, `FAILED/CANCELLED` остаётся failure screen и не предлагает archive.

### Шаг 4 — ready/download

Ready screen появляется только после terminal `COMPLETED` и успешного `GET /api/exports/{id}/bundle`. Он показывает delivery id, filename, size, SHA-256 и artifact summary.

Большой archive **не скачивается через Axios blob**, поскольку это потребовало бы буферизации потенциально большого bundle в памяти браузера. Вместо этого:

1. authenticated frontend вызывает `POST /api/exports/{id}/download-ticket`;
2. backend повторно проверяет owner/admin authorization и готовность bundle;
3. response устанавливает короткоживущий HttpOnly/SameSite=Strict cookie, ограниченный path конкретного download endpoint;
4. браузер выполняет обычную navigation download на `GET /api/exports/{id}/download`;
5. backend ещё раз проверяет ticket, активного пользователя, operation ownership и bundle metadata, затем отдаёт disk-backed `FileResponse`.

Ticket не является общей web-session cookie и не даёт доступ к другому operation id.

`.sha256` sidecar на ready screen формируется из уже verified backend metadata в стандартном виде:

```text
<sha256>  <archive-name>
```

Оператору явно предлагается перенести и `.htp.tar.gz`, и `.sha256`. SHA-256 используется для integrity/readiness и не подменяет Ed25519 signature внутри Bundle Protocol v1.

## Error UX

Frontend нормализует transport errors в безопасное user-facing сообщение и, когда backend предоставляет stable `{code, message}`, показывает этот код оператору. Raw upstream body/stderr не отображаются.

Поддерживаются отдельные состояния:

- loading;
- empty Harbor browse results;
- Harbor/backend unavailable;
- validation failure;
- wrong contour;
- insufficient role;
- operation failure/cancellation;
- completed bundle metadata/download error.

Failure state не предлагает partial archive как готовый к переносу.

## Доступность и responsive layout

Основные controls используют native `button`, `input`, `textarea`, `progress`, table semantics и явные labels. Focus-visible state предусмотрен для интерактивных элементов. Wizard перестраивает browse/summary grids на узком экране и не зависит от hover для основного действия.

## Структура

```text
frontend/src/
├── api/
│   ├── client.ts
│   └── exports.ts
├── components/
├── router/index.ts
├── stores/
│   ├── auth.ts
│   ├── runtime.ts
│   └── exportWizard.ts
├── styles/
└── views/
    ├── ExportView.vue
    ├── ImportView.vue
    └── ...
```

## Текущая готовность transfer UI

- SOURCE export wizard — реализован и использует реальные backend APIs;
- TARGET import backend orchestration — реализован отдельно;
- TARGET import wizard/UI — ещё не завершён и не должен документироваться как готовый пользовательский flow;
- history/audit/report UX — развивается отдельно.

## Локальная разработка и проверки

```bash
cd frontend
npm install
npm run dev
```

Перед merge frontend behavior:

```bash
npm run lint
npm run typecheck
npm test
npm run build
```
