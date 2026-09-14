# Стратегия тестирования и CI

**Статус:** актуальная test-selection и CI policy текущего `main`.

Этот документ определяет, какие проверки запускать для разных типов изменений Harbor Transfer Portal. Цель — не запускать весь тяжёлый набор после каждого локального изменения, но не пропускать проверки, соответствующие реальному blast radius.

Общие статусы документации и правила источников истины описаны в [docs/README.md](README.md).

## 1. Основные принципы

1. Проверки выбираются по затронутому поведению и зависимостям, а не только по расширению файла.
2. Красный test исправляется через root cause. Нельзя ослаблять assertions, добавлять `|| true`, `continue-on-error: true` или маскировать обязательную ошибку.
3. Runtime/integration tests не должны зависеть от публичного Harbor или внешнего registry. Используются mocks/local disposable fixtures.
4. Полный regression/E2E нужен перед release, после крупных shared/core изменений или когда blast radius нельзя надёжно ограничить.
5. CI на merge checkpoint является authoritative gate даже если локально агент запускал только scoped subset.
6. Planned test job не создаётся как пустой зелёный placeholder: gate появляется вместе с поведением, которое он реально проверяет.
7. Documentation gate не выполняет network crawling: локальная целостность репозитория должна проверяться детерминированно и без зависимости от внешних сайтов.

## 2. Текущие локальные gates

### Backend

```bash
make lint-backend
make test-backend
```

Текущий CI backend gate выполняет Ruff и backend unit/API pytest suite.

### Frontend

Frontend уже присутствует в репозитории. Текущий gate:

```bash
cd frontend
npm run lint
npm run typecheck
npm test
npm run build
```

CI выполняет ESLint, TypeScript, unit/component tests и production build.

### Documentation

```bash
make docs-check
```

Gate запускает:

```text
python3 -m unittest tools.test_check_doc_links
python3 tools/check_doc_links.py
```

Checker использует только Python stdlib и проверяет repository-relative Markdown links в root Markdown, `docs/**/*.md` и `deploy/**/*.md`.

External HTTP(S)/mailto/tel/data links не проверяются по сети. Это осознанно: docs-only CI не должен становиться flaky из-за третьего сайта или отсутствия internet access.

### Scoped CI selection

```bash
make ci-scope-check
```

Gate запускает stdlib-only regression suite `tools.test_ci_scope` для единого classifier `tools/ci_scope.py`. Тот же classifier вызывается непосредственно job `scope`, поэтому тесты и production CI selection не расходятся на две независимые реализации.

### Bundle Protocol / security regression

Для protocol/schema/domain изменений минимум:

```bash
cd backend
python -m pytest tests/test_bundle_protocol.py tests/test_bundle_schema.py
```

Этот gate защищает normative Bundle Protocol v1 и security-critical archive/schema behavior.

### Compose/runtime

```bash
./deploy/smoke-compose.sh
```

Compose smoke является обязательным для затронутого deployment/container/runtime scope.

## 3. Уровни тестирования

### Fast / unit

Используются для локальной бизнес-логики, domain transitions, API contracts, parsers/validators и UI components без внешнего service lifecycle.

Предпочтительный уровень, если дефект можно надёжно поймать быстро и локально.

### Component

Проверяет frontend component/view или backend component с ближайшими dependencies без полного transfer flow.

### Documentation integrity

Проверяет, что документационная навигация внутри репозитория не ведёт на отсутствующие файлы и не выходит за repository root.

Unit tests документационного checker покрывают:

- валидную relative-ссылку;
- отсутствующий target;
- external/pure-anchor skip;
- попытку path escape;
- fenced-code example;
- image path.

Anchor semantics внутри Markdown и доступность внешних URL пока не входят в gate. Их можно расширить отдельной задачей, если появится практическая потребность без ухудшения надёжности CI.

### Protocol / security regression

Обязателен для Bundle Protocol, package verifier/build, archive handling и других security-sensitive изменений.

Типовые regression cases уже включают или должны включаться одновременно с соответствующей реализацией:

- archive traversal / absolute paths;
- non-canonical path aliases;
- symlink/hardlink/special members;
- normalized duplicate/file-directory collisions;
- unsupported schema major;
- canonical manifest/signature tamper;
- payload checksum tamper;
- resource/member/path limits;
- secret/token redaction;
- subprocess argv без `shell=True`;
- auth/RBAC matrix.

Нельзя менять expected result только для того, чтобы security regression снова стал зелёным, если test обнаружил реальный defect.

### Integration

Запускается при изменении границ, где unit mocks недостаточны:

- Harbor client/settings;
- SQLAlchemy/Alembic persistence;
- Skopeo/Helm services;
- package service;
- export/import orchestration;
- Compose/runtime;
- межмодульные contracts.

Integration fixture должен быть локальным/disposable и не требовать public Harbor/internet во время выполнения application flow.

### Compose smoke

Текущий `deploy/smoke-compose.sh` проверяет не только старт контейнеров, но и runtime topology:

- `docker compose config`;
- build/healthy services;
- Nginx `/api/` proxy;
- contour configuration;
- Alembic heads;
- backend UID `10001`;
- Skopeo/Helm runtime versions;
- отсутствие backend secrets/config во frontend environment;
- persistence через restart/down-up;
- запуск тех же images в противоположном SOURCE/TARGET contour без rebuild/pull.

Поэтому изменения `deploy/*`, Compose/Docker/Nginx не считаются обычным docs-only scope даже если изменяется документация рядом с runtime scripts.

### E2E / release

Полный SOURCE → physical bundle → TARGET flow ещё не является текущим CI gate, потому что export/import orchestration v1 не завершена.

Перед release требуемый scenario должен включать как минимум:

1. local SOURCE registry fixture;
2. container image + Helm chart fixture;
3. SOURCE export;
4. signed bundle + `.sha256`;
5. перенос только разрешённых файлов;
6. отдельный TARGET registry без source dependency;
7. verification/preview/import;
8. target image digest verification;
9. chart result verification;
10. receipt/history;
11. idempotent replay;
12. conflict without automatic overwrite;
13. tampered bundle rejection before registry mutation.

Финальный release E2E относится к #28.

## 4. Матрица «изменение → проверки»

| Изменение | Минимальные проверки |
|---|---|
| Только backend service/API | Ruff + соответствующие backend tests |
| Auth/RBAC | backend auth tests + frontend role/session tests при затронутом UI |
| DB model/migration | backend tests + migration/persistence integration |
| Только frontend view/component | ESLint + typecheck + unit/component + build |
| Bundle protocol/schema/domain | docs-check + protocol/security regression + affected backend tests |
| Harbor client/settings | backend tests + mocked/integration Harbor scenarios |
| Skopeo | argv/redaction/timeout/path/digest tests + local integration при orchestration impact |
| Helm OCI | argv/redaction/timeout/archive/metadata tests + local integration при orchestration impact |
| Package verifier/build | backend + protocol/security regression |
| Export/import orchestration | unit + protocol/security + relevant integration |
| Compose/Docker/Nginx/deploy runtime | Compose config/build/smoke |
| Обычная docs-only правка | docs-check + quality-gate; тяжёлые code/E2E jobs skipped |
| `deploy/*.md` | docs-check + Compose smoke согласно current path policy |
| Workflow `.github/workflows/ci.yml` | все уже реализованные areas, включая docs, для проверки самого workflow |
| Release/install | полный required suite + E2E |

## 5. Path-aware GitHub Actions

Текущий workflow `.github/workflows/ci.yml` сохраняет event-specific discovery changed files, а path → CI-area policy вынесена в `tools/ci_scope.py`. Это один источник истины для runtime selection и regression tests.

Classifier выставляет outputs:

- `backend`;
- `frontend`;
- `protocol`;
- `compose`;
- `docs`.

Перед вычислением outputs job `scope` всегда выполняет `make ci-scope-check`. Helper и tests используют только Python stdlib и не требуют pip/npm/network.

### PR diff semantics

Для pull request changed files вычисляются относительно **merge base** base/head, а не прямым `base.sha → head.sha` diff.

Это важно для отставшей, но неконфликтующей ветки: изменения, которые уже попали в `main` после создания branch, не должны ошибочно считаться «изменениями PR» и запускать unrelated jobs.

Merge-base/push/workflow-dispatch discovery остаётся в `.github/workflows/ci.yml`; helper классифицирует уже найденный список путей и не изменяет Git semantics.

### Backend scope

Включается для `backend/*` и `Makefile`.

### Frontend scope

Включается для `frontend/*` и `Makefile`.

### Protocol scope

Включается для protocol/domain/schema/security-regression paths, в том числе `docs/offline-bundle-v1.md` и связанного ADR-009.

### Compose scope

Включается для:

- `compose.yaml`;
- `.dockerignore`;
- backend/frontend Dockerfiles;
- Nginx/entrypoint runtime files;
- `deploy/*`.

### Documentation scope

Включается для:

- root `README.md`/`CONTRIBUTING.md`;
- `docs/*`;
- Markdown в `deploy/`;
- documentation checker/tests;
- `Makefile`, потому что он содержит локальный `docs-check` entrypoint.

### Workflow self-test

Изменение `.github/workflows/ci.yml` включает все уже существующие applicable areas, включая documentation, чтобы workflow не мог изменить собственную логику без реальных checks.

После classification каждый area дополнительно gated наличием соответствующего компонента в проверяемой ревизии. Поэтому optional component, которого физически нет, не создаёт ложный job даже при workflow self-test.

Regression suite отдельно фиксирует docs-only, backend-only, frontend-only, backend+protocol, deploy runtime/Markdown, `Makefile`, workflow self-test, mixed diff и missing-component cases.

## 6. Documentation job

Job `Documentation — local links`:

1. checkout repository;
2. устанавливает Python 3.12 через уже используемый pinned setup action;
3. выполняет `make docs-check`;
4. не устанавливает дополнительные Python/npm packages и не обращается к внешним URL.

Missing local target или path escape возвращает non-zero и делает job красным.

## 7. `quality-gate`

Финальный job `quality-gate` выполняется всегда и зависит от:

- scope;
- backend;
- frontend;
- protocol;
- compose;
- docs.

Он принимает только:

- `success` для запущенного обязательного job;
- `skipped` для области, которая корректно признана незатронутой.

Любой другой результат делает gate красным.

Так docs-only PR запускает дешёвый documentation gate, но не полный transfer E2E.

## 8. Merge gate / branch protection

Для `main` в GitHub Rulesets/Branch protection рекомендуется/требуется включить:

- запрет merge при красных required checks;
- required status check: `CI / quality-gate`;
- требование актуальной ветки перед merge — после подтверждения, что оно не мешает согласованной параллельной работе команды.

Если connector/app не имеет administration permission для изменения branch protection, эта настройка остаётся действием владельца repository.

## 9. Dependency reproducibility

### Frontend

`frontend/package.json` существует, но `package-lock.json` в текущем `main` отсутствует. CI поэтому использует transition behavior:

- `npm ci` при наличии lockfile;
- иначе `npm install` с явным warning.

Для release/offline reproducibility lockfile должен стать обязательным.

### Backend

Python dependencies в `backend/pyproject.toml` используют совместимые version ranges. Финальная release/offline стратегия требует воспроизводимого constraints/lock approach.

Поэтому dependency reproducibility work #26 ещё не считается полностью завершённым только на основании рабочего CI baseline.

### Documentation checker

Не добавляет third-party dependency: checker и tests используют Python stdlib, поэтому docs gate не создаёт новый lock/supply-chain dependency.

## 10. Текущее состояние CI

Реально работающие current jobs после #67:

| Job | Статус |
|---|---|
| Scope detection | реализовано; classifier покрыт stdlib regression tests |
| Documentation local-link gate | реализовано |
| Backend Ruff + unit/API | реализовано |
| Frontend lint/type/unit/build | реализовано |
| Bundle Protocol contract/security regression | реализовано |
| Compose build/smoke | реализовано |
| Final `quality-gate` | реализовано |
| Skopeo/Helm disposable-registry integration | ещё требуется |
| Full SOURCE→TARGET E2E | ещё требуется после orchestration |
| Release/offline-install gate | ещё требуется в #28 |

## 11. Test selection examples

### Изменён только `frontend/src/views/LoginView.vue`

Запустить frontend lint/type/unit/build. Backend package/protocol/Compose не нужны, если contract/runtime не менялся.

### Изменён `backend/app/services/bundle_package_service.py`

Нужны affected backend tests + Bundle protocol/security regression. При изменении deployment/runtime boundary дополнительно Compose smoke.

### Изменён только `docs/architecture.md`

Запускается documentation gate + quality-gate. Backend/frontend/Compose не нужны.

### Изменён `deploy/README.md`

Запускаются docs-check и Compose smoke, поскольку `deploy/*` остаётся deployment scope, а Markdown одновременно относится к documentation scope.

### Отставшая docs-ветка не меняет deployment

PR scope определяется от merge base, поэтому уже merged изменение `deploy/README.md` в base не должно само по себе включить Compose job для такой ветки.

### Изменён `.github/workflows/ci.yml`

Запускаются все уже реализованные areas, включая docs, чтобы проверить сам механизм test selection.

## 12. Правило root cause

При падении проверки определить тип:

- product/code defect;
- documentation defect;
- test defect;
- environment/fixture defect;
- flaky behavior;
- contract mismatch;
- CI selection defect.

Исправляется первопричина. Нельзя:

- ослаблять assertion реального security/correctness invariant;
- скрывать exit code;
- отключать required job;
- использовать `continue-on-error` для обязательной проверки;
- добавлять исключение для сломанной локальной ссылки вместо исправления ссылки/структуры без документированной причины;
- превращать интеграционный defect в mock-only green test без объяснения.

## 13. Documentation impact

Если test/CI behavior меняется, в той же итерации обновить этот документ.

Если documentation-only path неожиданно запускает или пропускает тяжёлый job, проверить merge-base scope и intentional path policy (например `deploy/*.md` → docs + Compose), затем исправлять workflow или docs.

Связанные документы и source files:

- [Карта документации](README.md)
- [Архитектура](architecture.md)
- [Security](security.md)
- [CONTRIBUTING](../CONTRIBUTING.md)
- `.github/workflows/ci.yml`
- `tools/ci_scope.py`
- `tools/test_ci_scope.py`
- `tools/check_doc_links.py`

## 14. Remaining quality work

Следующие расширения не считаются реализованными только потому, что упомянуты здесь:

- backend static type gate;
- reproducible Python dependency lock/constraints;
- frontend lockfile;
- optional Markdown anchor validation, если будет оправдано;
- Skopeo/Helm local-registry integration;
- import/export orchestration integration;
- final SOURCE→TARGET E2E;
- offline release/install acceptance.

Они должны добавляться вместе с соответствующими implementation tasks и реальными fail-able tests, а не placeholder jobs.
