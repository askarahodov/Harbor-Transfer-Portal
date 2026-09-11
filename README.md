# Harbor Transfer Portal

![Harbor Transfer Portal](docs/img/devops-logo-white.png)

[![CI](https://github.com/askarahodov/Harbor-Transfer-Portal/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/askarahodov/Harbor-Transfer-Portal/actions/workflows/ci.yml)

**Harbor Transfer Portal** — локальный веб-портал для безопасной офлайн-передачи контейнерных образов и Helm OCI-чартов между двумя физически и сетево изолированными Harbor-контурами.

> **Статус:** проект активно развивается как **v1**. Текущая ветка `main` является веткой разработки и сама по себе не должна считаться готовым production-релизом или финальным offline installation kit.

**Не разработчик?** Начните с [паспорта проекта](docs/project-passport.md) — там назначение продукта, роли пользователей и сценарий передачи объяснены без необходимости знать Harbor CLI, Skopeo или Helm.

## За 30 секунд

Портал решает задачу передачи OCI-артефактов между контурами, между которыми **нет и не должно быть прямого сетевого соединения**.

```text
Harbor SOURCE
    ↓
Harbor Transfer Portal SOURCE
    ↓
подписанный Offline Bundle
    ↓
разрешённый физический носитель
    ↓
Harbor Transfer Portal TARGET
    ↓
проверка + импорт
    ↓
Harbor TARGET
```

Один и тот же продукт устанавливается отдельно в обоих контурах:

| Контур | Что делает портал |
|---|---|
| **SOURCE** | Читает только локальный Harbor, экспортирует выбранные images/charts и формирует проверяемый подписанный пакет |
| **TARGET** | Принимает пакет, проверяет его структуру, подпись и checksums, затем импортирует артефакты в свой локальный Harbor |

Прямая Harbor-to-Harbor репликация через границу изоляции не используется.

## Ключевые свойства

- каждая установка знает только свой локальный Harbor;
- SOURCE не хранит credentials TARGET и наоборот;
- контейнерные образы передаются через **Skopeo**, Helm OCI-чарты — через **Helm CLI**;
- Offline Bundle является версионированным протоколом доставки, а не произвольным архивом;
- manifest подписывается **Ed25519**, payload защищается SHA-256 checksums;
- конфликтующий tag/version с другим digest не перезаписывается автоматически;
- runtime в изолированном контуре не должен зависеть от интернета или CDN;
- успешный exit code отдельной команды не считается доказательством успешной доставки — результат фиксируется через доменное состояние операции и проверку артефактов.

## Кому куда идти

| Если вы... | Начните здесь |
|---|---|
| Хотите понять, что это за продукт | [Паспорт проекта](docs/project-passport.md) |
| Изучаете архитектуру и продуктовую модель | [Мастер-документ](docs/harbor-transfer-portal.md) |
| Разбираетесь с Offline Bundle v1 | [Протокол пакета](docs/offline-bundle-v1.md) |
| Разворачиваете текущий Compose-стек | [Deployment guide](deploy/README.md) |
| Проверяете security-модель | [Security](docs/security.md) |
| Работаете с CI и тестами | [Testing](docs/testing.md) |
| Хотите участвовать в разработке | [CONTRIBUTING](CONTRIBUTING.md) |
| Ищете принятые архитектурные решения | [ADR registry](docs/decisions.md) |

Полный user/admin/troubleshooting documentation set и финальный offline release kit входят в план v1 и отслеживаются задачами [#27](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/27) и [#28](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/28).

## Архитектура и стек

| Область | Технологии / ответственность |
|---|---|
| Backend | Python 3.12, FastAPI, доменная логика, REST API, transfer services |
| Frontend | Vue 3, Vite, TypeScript |
| Data | SQLite, SQLAlchemy, Alembic |
| Harbor integration | Harbor REST API v2.0 |
| Container images | Skopeo |
| Helm charts | Helm OCI |
| Deployment | Docker Compose, Nginx |
| Offline protocol | Versioned bundle, canonical manifest, Ed25519 signature, SHA-256 checksums |

Структура репозитория:

```text
backend/   FastAPI API, доменная логика и сервисы передачи
frontend/  Vue 3/Vite/TypeScript пользовательский интерфейс
docs/      архитектурная, протокольная и эксплуатационная документация
data/      локальные runtime-данные; сгенерированное содержимое не коммитится
deploy/    Docker Compose, smoke checks и материалы офлайн-поставки
```

Нормативное описание формата передачи находится в [docs/offline-bundle-v1.md](docs/offline-bundle-v1.md).

## Быстрый старт разработки

### Требования

- GNU Make;
- Python 3.12;
- Node.js 22+ и npm;
- Docker Engine с Docker Compose v2.

Подготовьте локальную конфигурацию:

```bash
cp .env.example .env
```

`.env.example` содержит только шаблонные значения. Реальные passwords, tokens, Harbor credentials и private keys не должны попадать в Git.

### Backend

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e './backend[dev]'

cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Базовые локальные endpoints:

```text
GET /api/health
GET /api/ready
GET /docs
```

Scoped-проверки backend из корня репозитория:

```bash
make lint-backend
make test-backend
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Проверки frontend:

```bash
npm run lint
npm run typecheck
npm test
npm run build
```

### Docker Compose

После заполнения `.env`:

```bash
make compose-config
make up
```

Настройка persistent data, bootstrap администратора и smoke test описаны в [deploy/README.md](deploy/README.md).

## Конфигурация контуров

`PORTAL_CONTOUR` принимает только:

```text
SOURCE
TARGET
```

Каждая установка использует один набор `HARBOR_*` для **своего локального Harbor**. Credentials противоположного контура в этой установке не настраиваются и не хранятся.

TLS verification включается по умолчанию. Для private PKI должна использоваться явная конфигурация custom CA, а не скрытое отключение проверки сертификата.

## Проверки и CI

GitHub Actions выбирает проверки по затронутой области diff:

- backend → Ruff + backend unit/API tests;
- frontend → ESLint + TypeScript + unit/component tests + production build;
- bundle protocol → contract/security regression;
- Compose/deployment → build/smoke path;
- `quality-gate` проверяет, что все обязательные для данного diff jobs завершились успешно.

Docs-only изменения не должны запускать тяжёлые несвязанные проверки. На merge checkpoint обязательный CI остаётся строгим: ошибки тестов и линтеров не маскируются через `|| true`, `continue-on-error` или аналогичные обходы.

Подробнее: [docs/testing.md](docs/testing.md).

## Security invariants

При разработке и эксплуатации считаются обязательными следующие правила:

- secrets и private signing keys не хранятся в Git;
- недоверенные значения не интерполируются в shell-команды;
- archive paths и bundle structure проверяются до controlled extraction;
- TARGET проверяет signature и payload checksums до registry mutation;
- конфликтующий digest не перезаписывается без явной разрешённой политики;
- health/readiness endpoints не раскрывают секретную конфигурацию;
- package/runtime не должен получать скрытую зависимость от публичного интернета.

Подробнее: [docs/security.md](docs/security.md) и [docs/offline-bundle-v1.md](docs/offline-bundle-v1.md).

## Разработка

Репозиторий разрабатывается людьми и ИИ-агентами по одинаковым инженерным правилам: сфокусированные PR, Conventional Commits, scoped tests, обязательный review и синхронизация документации с поведением кода.

Перед изменениями прочитайте [CONTRIBUTING.md](CONTRIBUTING.md).

Основной delivery roadmap ведётся через [EPIC #1](https://github.com/askarahodov/Harbor-Transfer-Portal/issues/1) и связанные issues.

## Лицензия

Проект распространяется по лицензии [Apache License 2.0](LICENSE).
