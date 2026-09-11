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
| Bundle protocol/schema/domain | protocol/security regression + affected backend tests |
| Harbor client/settings | backend tests + mocked/integration Harbor scenarios |
| Skopeo | argv/redaction/timeout/path/digest tests + local integration при orchestration impact |
| Helm OCI | argv/redaction/timeout/archive/metadata tests + local integration при orchestration impact |
| Package verifier/build | backend + protocol/security regression |
| Export/import orchestration | unit + protocol/security + relevant integration |
| Compose/Docker/Nginx/deploy runtime | Compose config/build/smoke |
| Обычная docs-only правка | scope detection + quality-gate; тяжёлые code/E2E jobs skipped |
| `deploy/*` docs/runtime | Compose smoke согласно current path policy |
| Workflow `.github/workflows/ci.yml` | все уже реализованные areas включаются для проверки самого workflow |
| Release/install | полный required suite + E2E |

## 5. Path-aware GitHub Actions

Текущий workflow `.github/workflows/ci.yml` вычисляет область diff и выставляет outputs:

- `backend`;
- `frontend`;
- `protocol`;
- `compose`.

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

### Workflow self-test

Изменение `.github/workflows/ci.yml` включает все уже существующие applicable areas, чтобы workflow не мог изменить собственную логику без реальных checks.

## 6. `quality-gate`

Финальный job `quality-gate` выполняется всегда.

Он принимает только:

- `success` для запущенного обязательного job;
- `skipped` для области, которая корректно признана незатронутой.

Любой другой результат делает gate красным.

Так docs-only PR не запускает полный transfer E2E, но required merge check остаётся единым и строгим.

## 7. Merge gate / branch protection

Для `main` в GitHub Rulesets/Branch protection рекомендуется/требуется включить:

- запрет merge при красных required checks;
- required status check: `CI / quality-gate`;
- требование актуальной ветки перед merge — после подтверждения, что оно не мешает согласованной параллельной работе команды.

Если connector/app не имеет administration permission для изменения branch protection, эта настройка остаётся действием владельца repository.

## 8. Dependency reproducibility

### Frontend

`frontend/package.json` существует, но `package-lock.json` в текущем `main` отсутствует. CI поэтому использует transition behavior:

- `npm ci` при наличии lockfile;
- иначе `npm install` с явным warning.

Для release/offline reproducibility lockfile должен стать обязательным.

### Backend

Python dependencies в `backend/pyproject.toml` используют совместимые version ranges. Финальная release/offline стратегия требует воспроизводимого constraints/lock approach.

Поэтому dependency reproducibility work #26 ещё не считается полностью завершённым только на основании рабочего CI baseline.

## 9. Текущее состояние CI

Реально работающие current jobs:

| Job | Статус |
|---|---|
| Scope detection | реализовано |
| Backend Ruff + unit/API | реализовано |
| Frontend lint/type/unit/build | реализовано |
| Bundle Protocol contract/security regression | реализовано |
| Compose build/smoke | реализовано |
| Final `quality-gate` | реализовано |
| Skopeo/Helm disposable-registry integration | ещё требуется |
| Full SOURCE→TARGET E2E | ещё требуется после orchestration |
| Release/offline-install gate | ещё требуется в #28 |

Это заменяет старую формулировку «job будет добавлен после появления frontend/Compose»: соответствующие компоненты и current jobs уже существуют.

## 10. Test selection examples

### Изменён только `frontend/src/views/LoginView.vue`

Запустить frontend lint/type/unit/build. Backend package/protocol/Compose не нужны, если contract/runtime не менялся.

### Изменён `backend/app/services/bundle_package_service.py`

Нужны affected backend tests + Bundle protocol/security regression. При изменении deployment/runtime boundary дополнительно Compose smoke.

### Изменён только `docs/architecture.md`

Обычный docs-only scope: heavy runtime jobs не требуются.

### Изменён `deploy/README.md`

Current CI path policy включает Compose smoke, поскольку `deploy/*` рассматривается как deployment scope. Это осознанно более строгий gate, чем для обычной документации.

### Изменён `.github/workflows/ci.yml`

Запускаются все уже реализованные areas, чтобы проверить сам механизм test selection.

## 11. Правило root cause

При падении проверки определить тип:

- product/code defect;
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
- превращать интеграционный дефект в mock-only green test без объяснения.

## 12. Documentation impact

Если test/CI behavior меняется, в той же итерации обновить этот документ.

Если documentation-only path неожиданно запускает или пропускает тяжёлый job, сначала проверить, является ли это intentional policy (например `deploy/*` → Compose), а затем исправлять workflow или docs.

Связанные документы:

- [Карта документации](README.md)
- [Архитектура](architecture.md)
- [Security](security.md)
- [CONTRIBUTING](../CONTRIBUTING.md)
- `.github/workflows/ci.yml`

## 13. Remaining quality work

Следующие расширения не считаются реализованными только потому, что упомянуты здесь:

- backend static type gate;
- reproducible Python dependency lock/constraints;
- frontend lockfile;
- Skopeo/Helm local-registry integration;
- import/export orchestration integration;
- final SOURCE→TARGET E2E;
- offline release/install acceptance.

Они должны добавляться вместе с соответствующими implementation tasks и реальными fail-able tests, а не placeholder jobs.