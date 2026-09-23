# Frontend

**Статус:** актуальное описание frontend текущей разработки v1.

## Стек

- Vue 3 + Vite + TypeScript;
- Vue Router;
- Pinia для application state;
- Axios для JSON/API requests и streaming browser upload;
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
| `/users` | `UsersView.vue` | `admin` | оба |
| `/settings` | `SettingsView.vue` | `admin` | оба |

Role/contour restrictions реализованы не только визуально. Vue Router guard проверяет роль и runtime contour; backend отдельно применяет собственный RBAC/contour policy и остаётся authoritative security boundary.

Sidebar также contour-aware: SOURCE operator/admin видит «Отправка», TARGET operator/admin — «Приём». Admin в обоих контурах дополнительно видит «Пользователи» и может управлять локальными учётными записями через `/users`. Пока contour не определён, frontend не угадывает transfer route.

## Определение контура

Основным источником контура является локальный `GET /api/health` с `contour: SOURCE|TARGET`. `frontend/public/runtime-config.js` используется только как offline-safe bootstrap fallback.

`runtime` Pinia store загружает contour и используется навигацией, route guards и transfer views. Страница не должна разрешать SOURCE/TARGET workflow только на основании URL route.

Для диагностики фактически запущенной сборки frontend image передаёт свой Git `VCS_REF` через offline-safe `runtime-config.js`. Верхняя панель показывает product version из backend health и короткий `UI <revision>` из самого frontend image. Это позволяет отличить новый backend от старого browser/frontend bundle при одинаковом product version. Для source checkout `make up` всегда выполняет build + force recreate текущего revision.

## Harbor profiles и workflow selection

Admin Settings содержит карточку **Harbor profiles** для управления именованными registry
profiles. Installation-wide selector в этой карточке явно обозначен как **legacy fallback**:
он нужен для backward-compatible клиентов без explicit profile id и не управляет новым
browser transfer workflow.

SOURCE Export и TARGET Import используют общий session-local selector. Список безопасных
enabled metadata загружается через `GET /api/harbor/profiles`. Выбор хранится только как
profile id в `sessionStorage`; credential/CA никогда не попадают в frontend.

В Export выбранный profile передаётся в Harbor browse/connection как query `profile_id`,
а preview/start — как `harbor_profile_id`. Смена profile до preview очищает project,
repository, artifact selection и зависимые search results.

В Import выбранный profile передаётся уже на upload/discovery boundary. После создания
operation UI читает persisted `harbor_profile_id/name/url` из operation response, фиксирует
selector read-only и передаёт тот же id в destination plan. Reload/resume поэтому
восстанавливает именно Harbor, закреплённый backend, а не текущую browser preference.

History показывает safe operation snapshot profile name/URL вместе с persisted transfer
evidence. Legacy operation без snapshot остаётся читаемой как legacy record.

Новый profile создаётся в Settings с name/URL/username/TLS и optional credential.
Credential отправляется отдельным request и после submission очищается из frontend state.
Connection test выполняется per-profile. Default Harbor form остаётся
bootstrap/backward-compatible настройкой profile `default`.

## Transfer policies и retention в Settings

Admin Settings использует общий `GET/PATCH /api/settings/transfer` contract. В блоке **Политики переноса**
есть отдельный subsection **Очистка transfer storage** с тремя controls:

- готовые SOURCE пакеты — срок хранения в сутках;
- failed/partial TARGET пакеты — срок хранения в сутках;
- период cleanup — в минутах.

Frontend только переводит display units в секунды для API. Штатные значения при отсутствии persisted override:
7 суток, 7 суток и 60 минут соответственно. Backend остаётся authoritative для bounds, persistence, audit и
active-operation safeguards. Эти изменения не требуют restart; только `operation_max_concurrent` в той же форме
сохраняет restart-required semantics.

## SOURCE export wizard

`ExportView.vue` реализует пользовательский flow задачи #18 поверх backend orchestration #17. State machine находится в `stores/exportWizard.ts`, typed API contract — в `api/exports.ts`.

### Шаг 1 — выбор

Wizard работает только с реальными Harbor browse endpoints. Верхний каскад использует reusable searchable comboboxes **Project → Repository**: repository недоступен до выбора project, а смена верхнего уровня сбрасывает зависимый browse state. Ниже расположен full-width **Version / tag** блок: server-backed search и постоянная таблица результатов с checkbox multi-select, reference, kind, digest, size и pushed time. Оператор может отметить несколько rows и одной кнопкой **«Добавить выбранное»** перенести их в transfer selection; уже добавленные artifacts отображаются отдельным списком и могут быть удалены до preview.

Selection хранит `kind`, `project`, `repository`, `reference` и pinned `digest`; immutable identity строится по kind/project/repository/digest, поэтому несколько aliases одного digest не создают несколько transfer items. Browse search/pagination остаются server-backed и не теряют уже добавленный выбор. `unknown-oci` не selectable и остаётся диагностическим. Для untagged container image используется digest fallback; Helm artifact без явной версии выбрать нельзя.

### Шаг 2 — preview

Frontend вызывает `POST /api/exports/preview` и показывает только подтверждённые backend значения: exact reference, authoritative SOURCE digest и размер. Отображаются SOURCE contour, состояние локального Harbor, оценка payload и operator comment с лимитом 2000 символов.

Preview не заменяет worker validation: backend повторно проверяет SOURCE после запуска операции.

### Шаг 3 — progress

После `POST /api/exports` wizard сохраняет `operation_id` в `sessionStorage` под ключом `htp.export.operation-id` и использует `GET /api/operations/{id}`.

Показываются persisted phase, completed/total artifacts, coarse progress только из backend counters, per-artifact state/error и elapsed time. ETA не выдумывается. При reload/reconnect сохранённый operation id открывается снова.

### Шаг 4 — ready/download

Ready screen появляется только после terminal `COMPLETED` и успешного `GET /api/exports/{id}/bundle`. Он показывает delivery id, filename, size, SHA-256 и artifact summary.

Большой archive **не скачивается через Axios blob**. Authenticated frontend получает scoped short-lived HttpOnly/SameSite=Strict download ticket и затем запускает обычный browser download на disk-backed `FileResponse`. Ticket ограничен конкретным operation download path.

`.sha256` sidecar на ready screen формируется из verified backend metadata:

```text
<sha256>  <archive-name>
```

Для browser physical handoff ready flow формирует комплект одной delivery: `.htp.tar.gz`, `.sha256` и signed `.htp-handoff.json`. Handoff связывает фактический archive/sidecar с SOURCE identity и проверяется TARGET до Bundle v1 preview. SHA-256 используется для integrity/readiness и не подменяет Ed25519 signature внутри Bundle Protocol v1.

## TARGET import wizard

`ImportView.vue` реализует пользовательский flow задачи #20 поверх backend orchestration #19. State machine находится в `stores/importWizard.ts`, typed API client — в `api/imports.ts`.

Главное правило UI: frontend **не выполняет криптографическую проверку самостоятельно и не выводит её успех из HTTP status**. Он показывает только verifier-derived projection, сохранённую backend после единственного `BundlePackageService.verify_bundle()` path.

### Шаг 1 — intake и verification

Поддерживаются два backend intake path.

**Browser physical handoff**:

- оператор выбирает комплект одной delivery: `.htp.tar.gz`, соответствующий `.sha256` и signed `.htp-handoff.json`;
- стандартные file inputs остаются keyboard-accessible альтернативой drag&drop;
- большой archive передаётся как raw stream без чтения всего bundle в JavaScript memory, а sidecar/handoff привязываются к той же intake operation;
- TARGET проверяет signed handoff до отображения Bundle v1 preview;
- frontend показывает локальный filename, size и browser upload progress;
- arbitrarily large browser upload не обещается.

Если backend возвращает `import_upload_too_large` или `operation_insufficient_disk`, UI предлагает безопасный large-bundle path: скопировать archive, соответствующий `.sha256` и signed `.htp-handoff.json` в configured incoming directory/transfer media и запустить discovery.

**Incoming discovery**:

- `POST /api/imports/discover` просит backend claim-ить только готовые delivery triplets: archive + `.sha256` sidecar + signed `.htp-handoff.json`;
- для нескольких найденных operations оператор выбирает нужную;
- имя/размер берутся из persisted operation metadata, а не из client-side filesystem assumptions.

После intake wizard сохраняет `operation_id` в `sessionStorage` под ключом `htp.import.operation-id`. Состояния `UPLOADED/DISCOVERED/VERIFYING` отображаются как package verification, а не как import Harbor.

Проверки показываются раздельно:

- SHA-256 integrity;
- Bundle v1 schema/canonical manifest;
- Ed25519 signature trust.

Успех каждого индикатора показывается только когда verified preview содержит соответствующий backend flag. При `REJECTED` UI показывает stable backend error и не предлагает execute.

### Шаг 2 — verified preview и policy

`GET /api/imports/{id}/preview` содержит signed/verified metadata:

- source delivery id;
- bundle filename/size/SHA-256 и intake mode;
- SOURCE Harbor identity и SOURCE portal version;
- manifest creation time, author и comment;
- signing key fingerprint и TARGET verification time;
- server-side `overwrite_allowed` policy;
- per-artifact TARGET classification.

Классификации отображаются без агрессивного объединения:

| Класс | UI policy |
|---|---|
| `NEW` | будет импортирован |
| `SAME` | уже соответствует expected digest; будет `SKIPPED` |
| `CONFLICT` | другой target digest; по умолчанию заблокирован |
| `UNKNOWN` | нельзя доказать безопасное состояние; execute заблокирован |
| `ERROR` | target inspection не удался; execute заблокирован |

Default button не может overwrite конфликт. Если conflicts присутствуют, overwrite action появляется только когда backend preview сообщает `overwrite_allowed=true` и текущая authenticated role имеет transfer permission. Даже тогда оператор обязан отметить отдельное confirmation рядом с **точным списком conflicting artifacts и digests**.

Frontend не ослабляет backend policy: execute endpoint заново проверяет READY state, unresolved classes, conflicts и server configuration.

### Шаг 3 — import/result

После `POST /api/imports/{id}/execute` UI использует generic `GET /api/operations/{id}` и показывает:

- `IMPORTING` / `VERIFYING_TARGET` phase;
- backend progress counters;
- каждый artifact отдельно;
- `VERIFIED`, `SKIPPED`, `CONFLICT`, `FAILED` и другие persisted states;
- source/target digest, если backend их знает;
- terminal operation status и safe error.

Browser reload/reconnect восстанавливает активную import operation из `sessionStorage`. Для `COMPLETED` и partial `FAILED` frontend пытается получить `GET /api/imports/{id}/receipt`.

Receipt показывается как immutable backend result и может быть сохранён оператором как небольшой JSON. Это не заменяет persisted receipt file backend. UI также предлагает переход к `/history`; History table, filters, receipt actions и CSV/PDF reports входят в current baseline.

При partial failure UI **не сообщает о rollback**: явно сказано, что уже успешно импортированные независимые artifacts автоматически не откатываются.

## Error UX

Frontend нормализует transport errors в безопасное user-facing сообщение и, когда backend предоставляет stable `{code, message}`, показывает этот код оператору. Raw upstream body/stderr не отображаются.

Поддерживаются отдельные состояния:

- loading/intake;
- empty incoming discovery;
- browser upload limit/disk failure с large-bundle guidance;
- cryptographic/schema rejection;
- wrong contour/insufficient role;
- unresolved TARGET state;
- conflict blocked by default;
- server-side overwrite disabled;
- operation failure/cancellation;
- receipt not ready.

Failure state никогда не превращается в успешный transfer только из-за наличия файла или route.

## Доступность и responsive layout

Основные controls используют native `button`, `input`, `textarea`, `progress`, table semantics и явные labels. Drag&drop не является единственным способом выбрать bundle. Focus-visible state предусмотрен для интерактивных элементов. Оба wizard перестраивают grids на узком экране и не зависят от hover для основного действия.

## Структура

```text
frontend/src/
├── api/
│   ├── client.ts
│   ├── exports.ts
│   ├── imports.ts
│   └── users.ts
├── components/
│   ├── WizardStepper.vue
│   ├── ExportArtifactSelector.vue
│   ├── ExportReadyCard.vue
│   ├── ImportVerificationCard.vue
│   └── ...
├── router/index.ts
├── stores/
│   ├── auth.ts
│   ├── runtime.ts
│   ├── exportWizard.ts
│   └── importWizard.ts
├── styles/
└── views/
    ├── ExportView.vue
    ├── ImportView.vue
    ├── UsersView.vue
    └── ...
```

## Текущая готовность transfer UI

- SOURCE export backend + wizard — реализованы и используют реальные APIs;
- TARGET intake/preview/import backend + wizard — реализованы и используют реальные APIs;
- admin user-management browser flow `/users` — реализован поверх admin-only `/api/users`;
- обе стороны восстанавливают persistent operation после reload;
- history/audit/report UX реализован и покрывает current persisted operations/reports;
- isolated SOURCE → physical transfer → TARGET acceptance и clean-host offline qualification входят в текущий CI/release gate.

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
