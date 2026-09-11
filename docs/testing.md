# Стратегия тестирования и CI

Этот документ определяет, какие проверки нужно запускать для разных типов изменений Harbor Transfer Portal. Цель — не запускать весь тяжёлый набор после каждого локального изменения, но не пропускать проверки, соответствующие реальному blast radius.

## Основные принципы

1. Проверки выбираются по затронутому поведению и зависимостям, а не только по расширению файла.
2. Красный тест исправляется через поиск первопричины. Нельзя ослаблять assertions, добавлять `|| true`, `continue-on-error: true` или иным способом маскировать обязательную ошибку.
3. Runtime-тесты не должны зависеть от публичного Harbor или внешнего registry. Интеграции строятся на mocks или локальных disposable fixtures.
4. Полный regression/E2E нужен перед релизом, после крупных shared/core изменений или когда blast radius нельзя надёжно ограничить.
5. Человекоориентированная документация тестовой стратегии ведётся на русском языке; технические имена jobs, commands, markers и API сохраняются без перевода.

## Уровни тестирования

### Fast / unit

Используются для локальной логики, API contracts и компонентов без внешних сервисов.

Текущий backend gate:

```bash
make lint-backend
make test-backend
```

Для frontend после появления `frontend/package.json`:

```bash
cd frontend
npm run lint
npm run typecheck
npm test
npm run build
```

### Protocol / security regression

Изменения Bundle Protocol v1, typed domain models и JSON Schema должны минимум запускать:

```bash
cd backend
python -m pytest tests/test_bundle_protocol.py tests/test_bundle_schema.py
```

По мере реализации package/import/security модулей в этот gate добавляются обязательные regression cases для:

- archive traversal и absolute paths;
- symlink/hardlink escape;
- tamper Ed25519 signature;
- tamper SHA-256 payload;
- unsupported schema major;
- secret/token redaction;
- безопасного subprocess argv без `shell=True`;
- RBAC/security matrix.

Наличие будущих проверок нельзя имитировать пустыми зелёными jobs. Они добавляются одновременно с реализацией соответствующего поведения.

### Integration

Запускается при изменении:

- Harbor client;
- SQLAlchemy/Alembic persistence;
- Skopeo/Helm services;
- package service;
- Compose/runtime;
- межмодульных контрактов.

Внешний интернет не является тестовой зависимостью самого приложения. GitHub-hosted CI может скачивать build dependencies, но после создания runtime image закрытый контур не должен обращаться в интернет.

### Compose smoke

При наличии `compose.yaml` и `deploy/smoke-compose.sh` CI запускает:

```bash
sh deploy/smoke-compose.sh
```

Smoke-test должен проверять не только статус контейнера, но и реальный proxy/API health, restart и persistence, версии runtime tools и отсутствие секретов Harbor во frontend-контейнере.

### E2E / release

Полный SOURCE → bundle → TARGET сценарий включается, когда реализованы export/import orchestration. До этого E2E job не создаётся как фиктивный placeholder.

Перед v1 release обязательный сценарий должен включать:

1. локальный SOURCE registry fixture;
2. export контейнерного образа и Helm chart;
3. создание подписанного bundle + `.sha256`;
4. физически эквивалентное копирование только разрешённых файлов;
5. отдельный TARGET registry fixture без сетевой зависимости от SOURCE;
6. verification/preview/import;
7. проверку итогового image digest;
8. idempotent replay;
9. conflict без overwrite по умолчанию;
10. tampered bundle, отклоняемый до registry mutation.

## Матрица «изменение → проверки»

| Изменение | Минимальные проверки |
| --- | --- |
| Только backend service/API | Ruff + соответствующие backend tests |
| Auth/RBAC | backend tests + auth/RBAC security cases |
| DB model/migration | backend tests + migration/persistence integration |
| Только frontend view/component | ESLint + typecheck + unit/component + build |
| Bundle protocol/schema/domain | protocol/security regression + затронутые backend tests |
| Harbor client | backend + mocked Harbor integration |
| Skopeo/Helm | unit argv/redaction/timeout + local integration fixture |
| Package/import | unit + protocol/security + integration |
| Compose/Docker/Nginx | Compose config/build/smoke |
| Только обычная документация | тяжёлые code/E2E jobs не запускаются |
| Release/install | полный required suite + E2E |

## Path-aware GitHub Actions

Workflow `.github/workflows/ci.yml` вычисляет область изменения через `git diff` и запускает только применимые jobs:

- `backend`;
- `frontend`;
- `protocol`;
- `compose`.

Финальный job `CI / quality-gate` выполняется всегда. Он считается успешным только если все jobs, требуемые для данного diff, имеют статус `success` или корректно `skipped` как незатронутые.

Это позволяет использовать один стабильный required check для branch protection, не превращая docs-only PR в полный transfer E2E.

## Branch protection / merge gate

Для `main` в GitHub Rulesets/Branch protection следует включить:

- запрет merge при красных required checks;
- required status check: **`CI / quality-gate`**;
- требование актуальной ветки перед merge рекомендуется после стабилизации CI и параллельного workflow команды.

GitHub App в текущем контексте не имеет отдельного действия для изменения branch protection, поэтому настройка фиксируется здесь как обязательная операция владельца репозитория.

## Dependency reproducibility

Python dependencies сейчас ограничены совместимыми version ranges в `backend/pyproject.toml`. Для release/offline kit потребуется отдельная воспроизводимая lock/constraints стратегия.

Frontend CI использует `npm ci`, если `package-lock.json` существует. До появления lockfile временно допускается `npm install` с явным предупреждением в CI. **Issue #26 не считается полностью завершённой, пока frontend lockfile и окончательная стратегия фиксации Python dependencies не внедрены.**

## Текущее состояние #26

Текущий CI baseline покрывает уже реализованные области: backend, Bundle Protocol v1, frontend при его появлении в PR и Compose при его появлении в PR.

Следующие расширения выполняются по мере готовности зависимых задач:

- полноценный static type gate backend;
- security regression package/import;
- Skopeo/Helm local-registry integration;
- final SOURCE→TARGET E2E;
- release/offline-install gate;
- CI badge после подтверждённого успешного workflow на `main`.
