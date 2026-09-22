# Стратегия тестирования и CI

**Статус:** актуальная test-selection и CI policy release-qualified baseline **v1.0.0**.

Этот документ определяет минимально достаточные проверки для разных типов изменений Harbor Transfer Portal. Источники истины для фактического CI selection — `.github/workflows/ci.yml`, `tools/ci_scope.py` и regression tests `tools/test_ci_scope.py`; этот документ объясняет их назначение и правила применения.

Общие статусы документации и приоритет источников истины описаны в [карте документации](README.md).

## 1. Основные принципы

1. Проверки выбираются по затронутому поведению, зависимостям и blast radius, а не по принципу «всегда запускать всё».
2. Красный test исправляется через root cause. Нельзя ослаблять assertions, скрывать exit code, добавлять `|| true` или `continue-on-error` для обязательной проверки.
3. Быстрый низкоуровневый test предпочтительнее тяжёлого E2E, если он надёжно защищает тот же contract.
4. Полный regression/release qualification нужен для release-sensitive/shared/runtime изменений или когда blast radius нельзя надёжно ограничить.
5. CI на merge checkpoint является authoritative gate; локальный scoped subset не заменяет итоговый `quality-gate`.
6. Test job не создаётся как фиктивный зелёный placeholder: gate существует вместе с реальным проверяемым поведением.
7. Логика CI scope сама покрыта regression tests.
8. Runtime/integration flow не должен зависеть от public Harbor/registry/internet после запуска fixture.
9. Documentation-only изменение не должно запускать несвязанные тяжёлые runtime jobs.

## 2. Локальные проверки

### Documentation

```bash
make docs-check
```

Запускает:

```text
python3 -m unittest tools.test_check_doc_links
python3 tools/check_doc_links.py
```

Checker проверяет repository-relative Markdown links в root Markdown, `docs/**/*.md` и `deploy/**/*.md`. External HTTP(S)/mailto/tel/data links не crawl-ятся.

### Backend

```bash
make lint-backend
make typecheck-backend
make test-backend
```

Backend gate выполняет Ruff → Mypy → pytest. `make test-backend` применяет application coverage floor `>=70%` для `backend/app`.

### Frontend

```bash
cd frontend
npm run lint
npm run typecheck
npm test
npm run build
```

CI устанавливает зависимости через `npm ci --no-audit --no-fund` и выполняет lint, typecheck, unit/component tests и production build.

### CI scope regression

```bash
make test-ci-scope
```

Проверяет behavior `tools/ci_scope.py`, включая docs-only, backend/frontend, protocol/security/integration, deployment и mixed diffs.

### Dependency lock invariants

```bash
make dependency-locks-check
```

Проверяет committed dependency locks до запуска scoped jobs. Backend использует `requirements-runtime.lock`/`requirements-dev.lock`, frontend — `package-lock.json` и `npm ci`.

### Skopeo/Helm integration

```bash
make test-registry-integration
```

Поднимает disposable local OCI registry и проверяет реальные production Skopeo/Helm binaries без зависимости application flow от public registry/internet.

### Offline-kit contract

```bash
make test-offline-kit
```

Проверяет packaging/install contract versioned offline kit.

### Compose/runtime

```bash
make smoke-compose
```

Проверяет runtime topology, health/readiness, migrations, persistence и container boundary. Этот gate относится к реальным deployment/runtime изменениям, а не к Markdown-файлам в `deploy/`.

## 3. Уровни тестирования

Используются следующие уровни:

- **Unit** — локальная domain/business logic;
- **Component** — frontend view/component или backend component с ближайшими dependencies;
- **Static type** — typed contracts до runtime tests;
- **Integration** — DB/API/OCI CLI/runtime boundaries;
- **Protocol regression** — normative Bundle v1/schema/package behavior;
- **Security regression** — auth/RBAC, archive/key/subprocess/import-export security boundaries;
- **Compose smoke** — container/runtime topology;
- **Acceptance E2E** — полный SOURCE → physical bundle → TARGET flow;
- **Release qualification** — clean-host offline installation и release identity.

Не дублируйте один и тот же behavior на всех уровнях без причины.

## 4. Реализованные CI jobs

`.github/workflows/ci.yml` содержит fail-able jobs:

| Job | Назначение |
|---|---|
| `Определение области изменений` | regression CI scope + lock invariants + path classification |
| `Backend — lint, types и unit/API tests` | Ruff, Mypy, pytest + coverage |
| `Frontend — lint, types, unit, build` | ESLint, TypeScript, unit/component, build |
| `Bundle protocol — contract regression` | Bundle v1/schema/package contract |
| `Security — targeted regression` | security-sensitive backend behavior |
| `Integration — Skopeo/Helm local registry` | real OCI CLI boundary |
| `Acceptance — isolated SOURCE → TARGET transfer` | full isolated transfer через physical bundle boundary |
| `Compose — build и smoke` | development/runtime Compose topology |
| `Offline release — clean-host install qualification` | immutable offline kit install в SOURCE/TARGET bootstrap roles |
| `Documentation — local links` | local Markdown integrity |
| `quality-gate` | агрегирует результаты всех требуемых jobs |

**Acceptance E2E и clean-host offline-install qualification реализованы и являются частью v1 release qualification.** Они больше не являются future work задачи #28.

## 5. Как выбирается CI scope

Для PR changed files вычисляются относительно merge base. Это важно для параллельной работы: уже merged изменения base не должны ошибочно попадать в scope отставшей, но неконфликтующей ветки.

`tools/ci_scope.py` выставляет области:

- `backend`;
- `frontend`;
- `protocol`;
- `security`;
- `integration`;
- `compose`;
- `docs`.

### Backend

Включается для `backend/*` и `Makefile`. Кроме того, deployment-файлы, которые напрямую проверяются backend cross-boundary regressions (`.env.example`, `compose.yaml`, `deploy/offline/compose.yaml`, `frontend/nginx.conf`), также включают backend gate. Это не позволяет runtime topology измениться при зелёном Compose smoke, оставив stale backend assertions незапущенными.

### Frontend

Включается для `frontend/*` и `Makefile`.

### Protocol

Включается для Bundle domain/schema/package paths, normative `docs/offline-bundle-v1.md`, `docs/schema/*` и protocol ADR-009.

### Security

Включается для security-sensitive backend boundaries: auth, import/export, package verifier/builder, key management, Harbor security-sensitive integration, Skopeo/Helm subprocess services и соответствующих tests.

Backend runtime dependency graph (`backend/pyproject.toml`, `requirements-runtime.lock`, `uv.lock`) также считается security-sensitive: изменение криптографической или другой runtime dependency должно проходить свежий targeted security regression даже без изменения application source.

Harbor profile domain/API также security-sensitive, потому что управляет registry credentials и custom CA. Изменения `harbor_profiles.py` и его regression tests включают targeted security job.

### Integration

Включается для реальных Skopeo/Helm/transfer runtime boundaries, включая relevant backend services/API, integration harness и release/acceptance scripts. Backend runtime dependency graph также включает integration scope, потому что смена resolved runtime packages может повлиять на полный transfer path без изменения его Python-файлов.

Когда `integration=true`, workflow запускает **оба**:

- `Integration — Skopeo/Helm local registry`;
- `Acceptance — isolated SOURCE → TARGET transfer`.

### Compose

Включается для runtime/container/deployment paths, включая:

- `compose.yaml`;
- `.dockerignore`;
- backend/frontend Dockerfiles;
- `frontend/nginx.conf` и runtime entrypoint files;
- non-Markdown files под `deploy/`.

Когда `compose=true`, workflow запускает:

- `Compose — build и smoke`;
- `Offline release — clean-host install qualification`.

**Markdown под `deploy/` специально исключён из Compose scope.** Например, изменение только `deploy/README.md` запускает documentation gate, но не Compose/offline qualification.

### Documentation

Включается для:

- root `README.md` и `CONTRIBUTING.md`;
- `docs/*`;
- `deploy/**/*.md`;
- documentation checker/tests;
- `Makefile`.

### CI policy self-test

Изменение `.github/workflows/ci.yml` или самого `tools/ci_scope.py` включает все существующие applicable areas. Scope job всегда сначала запускает regression `tools.test_ci_scope` и dependency-lock invariant, поэтому изменение механизма selection не может молча обойти policy.

## 6. Матрица «изменение → минимальные проверки»

| Изменение | Минимальный CI scope |
|---|---|
| Обычная docs-only правка | docs + quality-gate |
| `deploy/*.md` / `deploy/**/*.md` | docs + quality-gate |
| Только frontend view/component | frontend + quality-gate |
| Обычный backend service/API | backend + quality-gate |
| Backend runtime dependency graph | backend + security + integration + acceptance + quality-gate |
| Auth/RBAC/security-sensitive backend | backend + security + quality-gate |
| Bundle protocol/schema/package | backend + protocol + security по affected paths + quality-gate |
| Skopeo/Helm/transfer runtime boundary | backend/security по affected path + integration + acceptance + quality-gate |
| Dockerfile/runtime deployment/non-Markdown `deploy/*` | affected code gates + compose + offline-install + quality-gate |
| Browser/runtime trust boundary (`.env.example`, Compose, Nginx) | backend regression + affected frontend/compose gates + quality-gate |
| Workflow / CI scope policy | все существующие applicable areas + quality-gate |
| Release-sensitive mixed change | объединение всех affected areas; release gates не пропускаются |

## 7. Release qualification v1.0.0

### Isolated SOURCE → TARGET acceptance

`Acceptance — isolated SOURCE → TARGET transfer` уже реализован. Scenario проверяет как минимум:

1. отдельный SOURCE registry fixture;
2. container image и Helm chart fixture;
3. SOURCE export;
4. signed bundle + `.sha256`;
5. physical boundary — перенос только разрешённых files/trust material;
6. отдельный TARGET registry без runtime source dependency;
7. backend verification/preview/import;
8. target image digest verification;
9. Helm result/integrity semantics;
10. receipt/history/report;
11. idempotent replay → safe skip;
12. conflict → default deny;
13. tampered bundle → rejection до registry mutation.

### Clean-host offline installation

`Offline release — clean-host install qualification` уже реализован. Он собирает immutable kit, удаляет release-tagged images перед install phase, устанавливает один и тот же archive в SOURCE/TARGET bootstrap roles, проверяет exact local image identity/architecture, health/readiness, migrations, runtime tools, persistent state после rerun/restart и release version identity.

На push/workflow-dispatch qualified archive сохраняется как CI artifact вместе с внешним `.sha256`.

## 8. Protocol и security regression

### Bundle Protocol

Для protocol/schema/package boundary используется:

```bash
cd backend
python -m pytest \
  tests/test_bundle_protocol.py \
  tests/test_bundle_schema.py \
  tests/test_bundle_package_service.py \
  tests/test_bundle_package_key_bounds.py
```

Типовые invariants:

- archive traversal/absolute paths запрещены;
- symlink/hardlink/special members запрещены;
- unsupported schema major отклоняется;
- canonical manifest/signature tamper обнаруживается;
- payload checksum tamper обнаруживается;
- resource/member/path limits применяются fail closed.

### Security

Targeted security suite защищает:

- auth/RBAC/login throttling;
- Bundle build/verify/key bounds;
- export/import orchestration и publication guards;
- key-management lifecycle;
- Skopeo/Helm argv, metadata/digest behavior;
- structured logging redaction.

Нельзя менять expected result только для того, чтобы этот suite снова стал зелёным, если test обнаружил реальный defect.

## 9. Dependency reproducibility

### Frontend

`frontend/package-lock.json` обязателен; CI и Docker build используют:

```bash
npm ci --no-audit --no-fund
```

После намеренного изменения `package.json` обновите lock и проверьте invariant:

```bash
cd frontend
npm install --package-lock-only --ignore-scripts --no-audit --no-fund
cd ..
make dependency-locks-check
```

### Backend

Dependency intent находится в `backend/pyproject.toml`, resolved graphs — в:

- `backend/requirements-runtime.lock`;
- `backend/requirements-dev.lock`.

CI устанавливает dev graph без resolver drift:

```bash
python -m pip install -r backend/requirements-dev.lock
python -m pip install --no-deps --no-build-isolation ./backend
```

Backend image аналогично использует runtime lock. В закрытом runtime contour package resolution не выполняется: versioned offline kit уже содержит prebuilt images.

### Release identity

Product release version задаётся backend `app.__version__` и используется release/offline-kit tooling. `frontend/package.json` и root metadata `frontend/package-lock.json` обязаны содержать ту же product version; `make dependency-locks-check` fail-closed проверяет этот invariant, чтобы internal npm metadata не расходилась с `/api/health` и release artifacts.

## 10. `quality-gate`

`quality-gate` выполняется всегда и зависит от scope и всех потенциальных jobs.

Допустимые результаты для каждой области:

- `success` — job был обязателен и прошёл;
- `skipped` — область корректно признана незатронутой.

Любой failure/cancelled/unexpected result делает итоговый gate красным. Падение самого scope job также блокирует merge.

## 11. Примеры

### Только `docs/architecture.md`

Ожидается `Documentation — local links`; backend/frontend/protocol/security/integration/acceptance/compose/offline-install skipped; `quality-gate` success.

### Только `deploy/README.md`

Это documentation-only deployment guide change: docs job запускается, Compose и offline-install **не** запускаются.

### `backend/app/services/skopeo_service.py`

Включаются affected backend/security/integration areas; integration приводит также к isolated acceptance.

### `backend/Dockerfile`

Включаются backend + integration + compose; следовательно выполняются real registry integration, isolated acceptance, Compose smoke и clean-host offline-install qualification.

### `.github/workflows/ci.yml` или `tools/ci_scope.py`

Сначала scope regression и lock invariant, затем все существующие applicable areas, чтобы проверить сам механизм test selection.

## 12. Root cause при падении

Классифицируйте failure как:

- product/code defect;
- documentation defect;
- test defect;
- environment/fixture defect;
- flaky behavior;
- contract mismatch;
- CI selection defect.

Исправляйте первопричину. Запрещено маскировать обязательную ошибку, отключать required gate, подменять реальный integration defect mock-only test или добавлять runtime network egress вместо исправления fixture boundary.

## 13. Documentation impact

Если CI/test behavior меняется, `docs/testing.md`, `tools/ci_scope.py`, его regression tests и workflow должны оставаться согласованными в одной итерации.

Связанные источники:

- [Карта документации](README.md);
- [Архитектура](architecture.md);
- [Security](security.md);
- [CONTRIBUTING](../CONTRIBUTING.md);
- `.github/workflows/ci.yml`;
- `tools/ci_scope.py`;
- `tools/test_ci_scope.py`;
- `tools/check_dependency_locks.py`;
- `tools/check_doc_links.py`;
- `deploy/qualify-clean-offline-install.sh`;
- `deploy/qualify-isolated-transfer.sh`.

## 14. Remaining quality work

Реально незавершённые улучшения следует формулировать отдельно от уже существующих release gates. На текущем baseline это, например:

- optional Markdown anchor validation, если она будет оправдана без flaky external crawling;
- дополнительные targeted integration cases там, где существующих fixtures недостаточно.

Full SOURCE→TARGET acceptance и offline clean-host qualification **не относятся к remaining work**: они уже реализованы и входят в current CI policy.
