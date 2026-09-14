# Стратегия тестирования и CI

**Статус:** актуальная test-selection и CI policy текущего `main`.

Этот документ определяет, какие проверки запускать для разных типов изменений Harbor Transfer Portal. Цель — не запускать весь тяжёлый набор после каждого локального изменения, но не пропускать проверки, соответствующие реальному blast radius.

Общие статусы документации и правила источников истины описаны в [карте документации](README.md).

## 1. Основные принципы

1. Проверки выбираются по затронутому поведению и зависимостям, а не только по расширению файла.
2. Красный test исправляется через root cause. Нельзя ослаблять assertions, добавлять `|| true`, `continue-on-error: true` или маскировать обязательную ошибку.
3. Runtime/integration tests не должны зависеть от публичного Harbor или внешнего registry. Используются mocks/local disposable fixtures.
4. Полный regression/E2E нужен перед release, после крупных shared/core изменений или когда blast radius нельзя надёжно ограничить.
5. CI на merge checkpoint является authoritative gate даже если локально агент запускал только scoped subset.
6. Planned test job не создаётся как пустой зелёный placeholder: gate появляется вместе с поведением, которое он реально проверяет.
7. Логика выбора CI scope сама является тестируемым кодом: изменение classifier не должно незаметно расширять или сужать обязательные gates.
8. Documentation gate не выполняет network crawling: локальная целостность репозитория проверяется детерминированно без зависимости от внешних сайтов.

## 2. Текущие локальные gates

### Backend

```bash
make lint-backend
make test-backend
```

CI backend gate выполняет Ruff и полный backend unit/API pytest suite.

### Frontend

```bash
cd frontend
npm run lint
npm run typecheck
npm test
npm run build
```

CI выполняет ESLint, TypeScript, unit/component tests и production build.

### Scoped CI classifier

```bash
make test-ci-scope
```

Команда запускает stdlib-only regression suite `tools.test_ci_scope`. Тот же classifier `tools/ci_scope.py` используется job `Определение области изменений`, поэтому policy не дублируется между тестами и workflow.

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

### Bundle Protocol / security regression

Для protocol/schema/domain изменений минимум:

```bash
cd backend
python -m pytest tests/test_bundle_protocol.py tests/test_bundle_schema.py
```

Этот gate защищает normative Bundle Protocol v1 и security-critical archive/schema behavior.

### Compose/runtime

```bash
make smoke-compose
```

Compose smoke является обязательным для затронутого deployment/container/runtime scope.

## 3. Уровни тестирования

### Fast / unit

Используются для локальной бизнес-логики, domain transitions, API contracts, parsers/validators и UI components без внешнего service lifecycle.

Предпочтительный уровень, если дефект можно надёжно поймать быстро и локально.

### Component

Проверяет frontend component/view или backend component с ближайшими dependencies без полного transfer flow.

### CI-policy regression

`tools/test_ci_scope.py` фиксирует selection policy как behavior, а не как комментарий в YAML. Минимальная матрица включает:

- docs-only → только documentation;
- backend-only → backend;
- frontend-only → frontend;
- protocol domain path → backend + protocol;
- `deploy/README.md` → Compose + documentation;
- `Makefile` → backend + frontend + documentation;
- изменение `.github/workflows/ci.yml` → все существующие areas;
- mixed diff → объединение flags;
- отсутствие компонента в ревизии → отсутствие фиктивного job.

Scope job всегда запускает эти regression tests **до** вычисления outputs. Если classifier сломан, `scope` падает и финальный `quality-gate` не может стать зелёным.

### Documentation integrity

Проверяет, что документационная навигация внутри репозитория не ведёт на отсутствующие файлы и не выходит за repository root.

Unit tests документационного checker покрывают валидные relative links, missing targets, external/pure-anchor skip, path escape, fenced-code examples и image paths.

Anchor semantics внутри Markdown и доступность внешних URL пока не входят в gate. Их можно расширить отдельной задачей, если это можно сделать без ухудшения надёжности CI.

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

Текущий `deploy/smoke-compose.sh` проверяет runtime topology:

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

SOURCE export orchestration и TARGET import orchestration уже реализованы. Полный SOURCE → physical bundle → TARGET flow пока не является обычным PR gate не из-за отсутствия orchestration, а потому что он требует отдельного dual-contour/local-registry acceptance environment и относится к offline release qualification #28.

Перед release scenario должен включать как минимум:

1. local SOURCE registry fixture;
2. container image + Helm chart fixture;
3. SOURCE export;
4. signed bundle + `.sha256`;
5. перенос только разрешённых файлов;
6. отдельный TARGET registry без source dependency;
7. verification/preview/import;
8. target image digest verification;
9. chart result verification;
10. receipt/history/report;
11. idempotent replay;
12. conflict without automatic overwrite;
13. tampered bundle rejection before registry mutation.

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
| Workflow `.github/workflows/ci.yml` | scope-regression + все уже реализованные areas |
| Scope helper/tests | scope-regression всегда внутри `scope` job |
| Release/install | полный required suite + E2E |

## 5. Path-aware GitHub Actions

Workflow `.github/workflows/ci.yml` отвечает только за получение списка changed files:

- PR — diff от merge base base/head;
- push — diff `before → sha`;
- fallback/workflow dispatch — `git ls-files`.

Само преобразование changed paths в areas выполняет `tools/ci_scope.py`. Он выставляет outputs:

- `backend`;
- `frontend`;
- `protocol`;
- `compose`;
- `docs`.

### PR diff semantics

Для pull request changed files вычисляются относительно **merge base**, а не прямым `base.sha → head.sha` diff.

Это важно для отставшей, но неконфликтующей ветки: изменения, которые уже попали в `main` после создания branch, не должны ошибочно считаться изменениями PR и запускать unrelated jobs.

### Backend scope

Включается для `backend/*` и `Makefile`.

### Frontend scope

Включается для `frontend/*` и `Makefile`.

### Protocol scope

Включается для protocol/domain/schema paths, в том числе `backend/app/domain/*`, protocol/schema regression files, `docs/offline-bundle-v1.md`, `docs/schema/*` и связанного ADR-009.

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
- `Makefile`.

### Workflow self-test

Изменение `.github/workflows/ci.yml` включает все реально существующие applicable areas. Перед classification scope job всегда выполняет `python3 -m unittest tools.test_ci_scope`, поэтому изменение workflow или classifier не может обойти regression policy молча.

После classification helper повторно проверяет наличие component markers (`backend/pyproject.toml`, `frontend/package.json`, protocol test, Compose smoke script, docs checker) и не создаёт job для компонента, которого нет в проверяемой ревизии.

## 6. Documentation job

Job `Documentation — local links`:

1. checkout repository;
2. устанавливает Python 3.12 через pinned setup action;
3. выполняет `make docs-check`;
4. не устанавливает дополнительные Python/npm packages и не обращается к внешним URL.

Missing local target или path escape возвращает non-zero и делает job красным.

## 7. `quality-gate`

Финальный `quality-gate` выполняется всегда и зависит от scope/backend/frontend/protocol/compose/docs.

Он принимает только:

- `success` для запущенного обязательного job;
- `skipped` для области, которая корректно признана незатронутой.

Любой другой результат делает gate красным. Падение самого `scope` job, включая его regression suite, также красит `quality-gate`.

## 8. Merge gate / branch protection

Для `main` в GitHub Rulesets/Branch protection требуется/рекомендуется:

- запрет merge при красных required checks;
- required status check: `CI / quality-gate`;
- требование актуальной ветки перед merge — только если оно не ломает согласованную параллельную работу команды.

Если connector/app не имеет administration permission для изменения branch protection, эта настройка остаётся действием владельца repository.

## 9. Dependency reproducibility

### Frontend

`frontend/package.json` существует, но `package-lock.json` в текущем `main` отсутствует. CI использует переходный режим:

- `npm ci` при наличии lockfile;
- иначе `npm install` с явным warning.

Для release/offline reproducibility lockfile должен стать обязательным.

### Backend

Python dependencies в `backend/pyproject.toml` используют compatible version ranges. Финальная release/offline стратегия требует воспроизводимого constraints/lock approach.

Поэтому dependency reproducibility work #26 ещё не считается завершённым только на основании рабочего CI baseline.

### CI-scope и documentation helpers

`tools/ci_scope.py`, `tools/test_ci_scope.py`, documentation checker и его tests используют только Python stdlib. Scope/documentation gates не создают дополнительную supply-chain dependency.

## 10. Текущее состояние CI

| Job/capability | Статус |
|---|---|
| Scope detection | реализовано; classifier regression-tested |
| Documentation local-link gate | реализовано |
| Backend Ruff + unit/API | реализовано |
| Frontend lint/type/unit/build | реализовано |
| Bundle Protocol contract/security regression | реализовано |
| Compose build/smoke | реализовано |
| Final `quality-gate` | реализовано |
| Skopeo/Helm disposable-registry integration | ещё требуется |
| Full SOURCE→TARGET dual-contour E2E | требуется в #28 |
| Release/offline-install gate | требуется в #28 |

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

Сначала запускается regression suite classifier, затем включаются все уже реализованные areas, чтобы проверить сам механизм test selection.

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

Если documentation-only path неожиданно запускает или пропускает тяжёлый job, сначала воспроизвести expected mapping через `make test-ci-scope`, затем исправлять classifier/tests вместе как один contract.

Связанные документы:

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
- additional export/import integration where mocks are insufficient;
- final SOURCE→TARGET E2E;
- offline release/install acceptance.

Они должны добавляться вместе с соответствующими implementation tasks и реальными fail-able tests, а не placeholder jobs.
