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
python3 -m unittest tools.test_check_doc_links tools.test_documentation_contracts
python3 tools/check_doc_links.py
```

Checker проверяет repository-relative Markdown links в root Markdown, `docs/**/*.md` и `deploy/**/*.md`. External HTTP(S)/mailto/tel/data links не crawl-ятся. Documentation contract tests защищают смысловые инварианты документов, в том числе соответствие таблицы реализованных CI jobs фактическим `name:` в `.github/workflows/ci.yml`.

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

Проверяет committed dependency locks до запуска scoped jobs. Backend устанавливается из `requirements-runtime.lock`/`requirements-dev.lock`; их общие registry-пакеты должны иметь те же версии, что и в `backend/uv.lock`. Иначе CI мог бы быть зелёным, проверяя старую версию после Dependabot-обновления только `uv.lock`. Build-only зависимости, отсутствующие в uv resolution, в этом сравнении не учитываются. Frontend использует `package-lock.json` и `npm ci`.

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
- **Acceptance E2E** — полный SOURCE → physical bundle → TARGET flow: автоматизированный isolated acceptance в `Integration — интеграционные тесты` плюс внешняя региональная приёмка (см. раздел 7);
- **Release qualification** — clean-host offline installation и release identity: clean-host qualification в `Integration — интеграционные тесты`, Windows mock-Docker contracts в `Windows Offline — install and lifecycle contracts` и внешняя Windows Docker Desktop приёмка (см. раздел 7).

Не дублируйте один и тот же behavior на всех уровнях без причины.

## 4. Реализованные CI jobs

`.github/workflows/ci.yml` содержит fail-able jobs:

| Job | Назначение |
|---|---|
| `Определение области изменений` | regression CI scope, dependency lock invariants, path classification |
| `Backend — lint, types и unit/API tests` | Ruff, Mypy, pytest + coverage |
| `Frontend — lint, types, unit, build` | ESLint, TypeScript, unit/component, build |
| `Protocol — проверка совместимости` | Bundle v1/schema/package pytest |
| `Security — проверка уязвимостей` | Bandit (high/medium) и Trivy filesystem scan (CRITICAL/HIGH) |
| `Integration — интеграционные тесты` | real Skopeo/Helm boundary (`make test-registry-integration`), clean-host offline qualification, isolated SOURCE → TARGET acceptance и gated-шаги `Nexus … — Docker v2 transport` |
| `Compose — проверка docker-compose` | `make test-offline-kit`, config, build, запуск стека и `/api/health` |
| `Windows CMD — launcher regression (mock Docker)` | CMD launcher regression с mock Docker (Windows runner) |
| `Windows Offline — install and lifecycle contracts` | PowerShell prebuilt-installer и lifecycle safety contracts с mock Docker (Windows runner) |
| `Docs — проверка документации` | `make docs-check`: local Markdown links и documentation contracts |
| `Quality Gate` | агрегирует результаты всех требуемых jobs |

`Integration — интеграционные тесты` выполняет `deploy/qualify-clean-offline-install.sh` (clean-host SOURCE/TARGET install qualification) и `deploy/qualify-isolated-transfer.sh` (isolated SOURCE → physical bundle → TARGET acceptance) и публикует прошедший проверку offline kit как GitHub Actions artifact. В том же job отдельным набором шагов выполняется реальный Nexus legacy Docker v2 transport (`Nexus … — Docker v2 transport`: pre-OCI `sonatype/nexus3` <3.94, self-signed TLS proxy, `pytest backend/tests/test_nexus_legacy_e2e.py`); эти шаги включаются только при `nexus_e2e=true` (см. раздел 5) или явном `workflow_dispatch` с input `nexus_version`, отдельного workflow больше нет. `Windows Offline — install and lifecycle contracts` проверяет Windows PowerShell wrappers с mock Docker; реальная Windows 10/11 Docker Desktop приёмка остаётся внешним gate (см. раздел 7 и issue [#507](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/507)).

## 5. Как выбирается CI scope

Для PR changed files вычисляются относительно merge base. Это важно для параллельной работы: уже merged изменения base не должны ошибочно попадать в scope отставшей, но неконфликтующей ветки.

`tools/ci_scope.py` выставляет области:

- `backend`;
- `frontend`;
- `protocol`;
- `security`;
- `integration`;
- `compose`;
- `docs`;
- `nexus_e2e`.

### Backend

Включается для `backend/*` и `Makefile`. Кроме того, deployment-файлы, которые напрямую проверяются backend cross-boundary regressions (`.env.example`, `compose.yaml`, `deploy/offline/compose.yaml`, `frontend/nginx.conf`), также включают backend gate. Это не позволяет runtime topology измениться при зелёном `Compose — проверка docker-compose`, оставив stale backend assertions незапущенными.

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

Когда `integration=true`, workflow запускает `Integration — интеграционные тесты`, который выполняет `make test-registry-integration` (disposable local OCI registry + реальные Skopeo/Helm), затем `deploy/qualify-clean-offline-install.sh` (clean-host offline qualification) и `deploy/qualify-isolated-transfer.sh` (isolated SOURCE → TARGET acceptance), после чего публикует прошедший qualification offline kit как artifact.

Изменение любого non-Markdown файла под `deploy/offline/` (Linux lifecycle `*.sh`, Windows PowerShell/CMD wrappers, `compose.yaml`, `.env.example`) и `deploy/build-offline-kit.sh` включает **и** `integration`, **и** `compose`: Linux clean-host qualification исполняет реальный offline lifecycle, поэтому контракт нельзя проверять только Windows mock-Docker contracts. Markdown под `deploy/offline/` остаётся только `docs`.

### Nexus E2E

Включается для путей, от которых зависит реальный Nexus legacy Docker v2 transport: `tools/nexus_e2e_setup.py`, `backend/tests/test_nexus_legacy_e2e.py`, `backend/app/services/nexus_service.py`, `backend/app/services/registry_client.py`, `backend/requirements-dev.lock`, `docs/nexus-setup.md`. Когда `nexus_e2e=true`, `Integration — интеграционные тесты` дополнительно выполняет gated-шаги Nexus Docker v2 E2E. При `workflow_dispatch` они выполняются всегда (с `nexus_version`, по умолчанию `3.69.0`). Изменение `.github/workflows/ci.yml` или `tools/ci_scope.py` также включает `nexus_e2e`, когда marker-файлы E2E присутствуют.

Отдельного job для Nexus нет: E2E-шаги живут внутри `Integration — интеграционные тесты`, который запускается при `integration=true` **или** `nexus_e2e=true`. Поэтому `quality-gate` считает этот job обязательным, если включена любая из этих областей (`INTEGRATION_REQUIRED = integration == 'true' || nexus_e2e == 'true'`); `skipped` для него допустим только когда обе области false. Иначе `nexus_e2e`-only прогон (integration=false) с падением E2E давал бы «unexpected result», а смешанный прогон мог бы скрыть сбой. Это правило закреплено regression test в `tools/test_ci_scope.py`.

### Compose

Включается для runtime/container/deployment paths, включая:

- `compose.yaml`, `compose.online.yaml`;
- `.dockerignore`, `.gitattributes`;
- backend/frontend Dockerfiles и `frontend/docker-entrypoint.d`;
- launchers `run.sh`, `run.cmd`, `run-online.sh`, `run-online.cmd`, `tools/test_windows_launcher.ps1`;
- Windows offline contracts `tools/test_install_windows.ps1`, `tools/test_windows_offline_lifecycle.ps1`;
- `frontend/nginx.conf`;
- non-Markdown files под `deploy/`.

Когда `compose=true`, workflow запускает job'ы с этим condition:

- `Compose — проверка docker-compose` — `make test-offline-kit`, затем `docker compose config`, `build`, `up --wait` и проверка `/api/health`;
- `Windows CMD — launcher regression (mock Docker)` на `windows-2022`;
- `Windows Offline — install and lifecycle contracts` на `windows-2022`.

`Compose — проверка docker-compose` запускает `make test-offline-kit`, но не `make smoke-compose`, потому что вызывает `docker compose` напрямую. Оба Windows jobs используют mock Docker без реального runtime, поэтому реальная Windows Docker Desktop clean-host приёмка остаётся внешним gate.

**Markdown под `deploy/` специально исключён из Compose scope.** Например, изменение только `deploy/README.md` запускает documentation gate, но не Compose.

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
| Backend runtime dependency graph | backend + security + integration + quality-gate |
| Auth/RBAC/security-sensitive backend | backend + security + quality-gate |
| Bundle protocol/schema/package | backend + protocol + security по affected paths + quality-gate |
| Skopeo/Helm/transfer runtime boundary | backend/security по affected path + integration + quality-gate |
| Dockerfile/runtime deployment/non-Markdown `deploy/*` | affected code gates + compose + windows-launcher + windows-offline-lifecycle + quality-gate |
| non-Markdown `deploy/offline/*` / `deploy/build-offline-kit.sh` | integration + compose + windows-launcher + windows-offline-lifecycle + quality-gate |
| `deploy/**/*.md` | docs + quality-gate |
| Browser/runtime trust boundary (`.env.example`, Compose, Nginx) | backend regression + affected frontend/compose gates + quality-gate |
| Workflow / CI scope policy | все существующие applicable areas + quality-gate |
| Release-sensitive mixed change | объединение всех affected areas; release gates не пропускаются |

## 7. Release qualification v1.0.0

CI выполняет jobs из раздела 4: scoped backend/frontend/protocol/security/integration/compose/windows-launcher/windows-offline-lifecycle/docs и агрегирующий `Quality Gate`. `Integration — интеграционные тесты` (при `integration=true`) выполняет clean-host offline qualification и isolated SOURCE → TARGET acceptance; `Windows Offline — install and lifecycle contracts` (при `compose=true`) проверяет Windows PowerShell contracts с mock Docker.

### Что проверяется сейчас

- `Compose — проверка docker-compose`: `make test-offline-kit`, config, build, запуск development/runtime стека и `/api/health`.
- `Integration — интеграционные тесты`: disposable local OCI registry + реальные Skopeo/Helm binaries (`make test-registry-integration`), затем clean-host offline qualification и isolated SOURCE → TARGET acceptance; прошедший kit публикуется как artifact.
- `Windows Offline — install and lifecycle contracts`: prebuilt installer и lifecycle safety contracts с mock Docker на `windows-2022`.
- `make smoke-compose` остаётся локальной проверкой; CI запускает его компоненты напрямую (`make test-offline-kit` и `docker compose`).

### Внешние release gates

Следующие скрипты выполняются в CI (`Integration — интеграционные тесты`) и остаются доступными standalone для внешнего release-прогона:

- **Isolated SOURCE → TARGET acceptance** — `deploy/qualify-isolated-transfer.sh`. Scenario покрывает как минимум:
  1. отдельный SOURCE registry fixture;
  2. container image и Helm chart fixture;
  3. SOURCE export;
  4. полный signed delivery triplet: bundle + `.sha256` + `.htp-handoff.json`;
  5. physical boundary — перенос только разрешённых delivery/trust files и handoff verification до Bundle preview;
  6. отдельный TARGET registry без runtime source dependency;
  7. backend verification/preview/import;
  8. target image digest verification;
  9. Helm result/integrity semantics;
  10. receipt/history/report;
  11. idempotent replay → safe skip;
  12. conflict → default deny;
  13. tampered bundle → rejection до registry mutation.
- **Clean-host offline installation** — `deploy/qualify-clean-offline-install.sh`. Собирает immutable kit, требует отсутствия release-tagged images перед install phase, устанавливает один и тот же archive в SOURCE/TARGET bootstrap roles, проверяет exact local image identity/architecture, health/readiness, migrations, runtime tools, persistent state после rerun/restart и release version identity.

Эти скрипты являются обязательными автоматизированными CI gates ([#507](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/507); для Nexus — [#434](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/434)/[#436](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/436)). Зелёный `Quality Gate` **не заменяет** внешнюю приёмку: реальная Windows 10/11 Docker Desktop clean-host проверка и региональная Nexus приёмка требуют отдельного стенда и evidence.

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

`quality-gate` выполняется всегда и зависит от scope и всех потенциальных jobs. Области проверяются через scope outputs: backend, frontend, protocol, security, compose, windows-launcher, windows-offline-lifecycle, docs, а также `integration` и `nexus_e2e` (оба относятся к общему job `Integration — интеграционные тесты`, поэтому он обязателен, если включена любая из этих двух областей).

Допустимые результаты для каждой области:

- `success` — job был обязателен и прошёл;
- `skipped` — область корректно признана незатронутой.

Любой failure/cancelled/unexpected result делает итоговый gate красным. Падение самого scope job также блокирует merge.

## 11. Примеры

### Только `docs/architecture.md`

Ожидается `Docs — проверка документации`; остальные scoped jobs skipped; `Quality Gate` success.

### Только `deploy/README.md`

Это documentation-only deployment guide change: `Docs — проверка документации` запускается, Compose, Windows CMD и Windows Offline jobs **не** запускаются.

### `backend/app/services/skopeo_service.py`

Включаются affected backend/security/integration areas; `Integration — интеграционные тесты` выполняет real registry integration, clean-host offline qualification и isolated acceptance.

### `backend/Dockerfile`

Включаются backend + integration + compose; следовательно выполняются `Integration — интеграционные тесты` (включая clean-host qualification и isolated acceptance), `Compose — проверка docker-compose`, `Windows CMD — launcher regression (mock Docker)` и `Windows Offline — install and lifecycle contracts`.

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

Full SOURCE→TARGET acceptance и clean-host offline qualification **являются частью CI**: `Integration — интеграционные тесты` запускает `deploy/qualify-clean-offline-install.sh` и `deploy/qualify-isolated-transfer.sh`, а `Windows Offline — install and lifecycle contracts` покрывает Windows mock-Docker contracts (issue [#507](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/507)). Реальная Windows Docker Desktop и региональная Nexus приёмка остаются внешними gates.
