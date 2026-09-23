# Harbor Transfer Portal

![Harbor Transfer Portal](docs/img/devops-logo-white.png)

[![CI](https://github.com/askarahodov/Harbor-Transfer-Portal/actions/workflows/ci.yml/badge.svg)](https://github.com/askarahodov/Harbor-Transfer-Portal/actions/workflows/ci.yml)

**Harbor Transfer Portal** — локальный веб-портал для безопасной офлайн-передачи container images и Helm OCI charts между физически и сетево изолированными Harbor-контурами.

Портал используется, когда SOURCE и TARGET не имеют прямого сетевого соединения: пакет формируется на SOURCE, переносится разрешённым физическим способом и проверяется перед импортом на TARGET.

> **Статус:** функциональный объём **v1.0.0** реализован и прошёл release qualification. Production rollout требует локального change/release approval и проверки инфраструктуры конкретного контура.

## Интерфейс

![Dashboard](docs/img/screenshots/dashboard.png)

## Быстрый старт

### Требования

- Docker Engine + Docker Compose v2
- Git
- Windows 10/11 (с WSL 2) или Linux

### Windows (PowerShell)

```powershell
# 1. Клонировать и настроить
git clone https://github.com/askarahodov/Harbor-Transfer-Portal.git
cd Harbor-Transfer-Portal
cp .env.example .env
# Отредактируйте .env: PORTAL_CONTOUR, HARBOR_URL, HARBOR_USER, HARBOR_PASSWORD, JWT_SECRET

# 2. Запуск
docker compose up -d --build

# 3. Создать админа (первый запуск)
$env:BOOTSTRAP_ADMIN_PASSWORD = "ваш-пароль-12+"
docker compose exec -T -e BOOTSTRAP_ADMIN_PASSWORD=$env:BOOTSTRAP_ADMIN_PASSWORD backend python -m app.auth.cli --username admin
Remove-Item env:BOOTSTRAP_ADMIN_PASSWORD

# 4. Открыть в браузере
# http://localhost:8080  (web UI)
# http://localhost:8080/docs  (OpenAPI)
```

### Linux / WSL 2

```bash
# 1. Клонировать и настроить
git clone https://github.com/askarahodov/Harbor-Transfer-Portal.git
cd Harbor-Transfer-Portal
cp .env.example .env
# Отредактируйте .env: PORTAL_CONTOUR, HARBOR_URL, HARBOR_USER, HARBOR_PASSWORD, JWT_SECRET

# 2. Запуск
docker compose up -d --build

# 3. Создать админа (первый запуск)
export BOOTSTRAP_ADMIN_PASSWORD="ваш-пароль-12+"
docker compose exec -T -e BOOTSTRAP_ADMIN_PASSWORD="$BOOTSTRAP_ADMIN_PASSWORD" backend python -m app.auth.cli --username admin
unset BOOTSTRAP_ADMIN_PASSWORD

# 4. Открыть в браузере
# http://localhost:8080  (web UI)
# http://localhost:8080/docs  (OpenAPI)
```

### Обязательные переменные в `.env`

| Переменная | Описание | Пример |
|---|---|---|
| `PORTAL_CONTOUR` | `SOURCE` или `TARGET` | `SOURCE` |
| `HARBOR_URL` | URL локального Harbor | `https://harbor.local` |
| `HARBOR_USER` | Service account | `transfer-bot` |
| `HARBOR_PASSWORD` | Пароль или файл секрета | `secret` |
| `JWT_SECRET` | Случайный секрет ≥32 символа | `openssl rand -base64 48` |

### Полезные команды

```bash
docker compose ps           # Статус сервисов
docker compose logs -f      # Логи в реальном времени
docker compose down         # Остановить (данные сохраняются)
docker compose down -v      # Остановить и удалить данные
docker compose restart      # Перезапуск
docker compose up -d --build # Пересборка и запуск
```

### Доступ

- **Web UI:** `http://localhost:8080`
- **OpenAPI:** `http://localhost:8080/docs`
- **Health:** `http://localhost:8080/api/health`
- **Docsify (встроенная документация):** `http://localhost:8080/docs/`

### Встроенная документация

После запуска полная документация доступна через встроенный Docsify на том же адресе:
`http://127.0.0.1:8080/docs/` (при стандартном `PORTAL_HTTP_PORT=8080`).
Docsify JS/CSS включены во frontend image, поэтому для чтения документации в закрытом контуре не требуется Internet/CDN.

| Если вы… | Откройте |
|---|---|
| выполняете перенос | [Пользовательское руководство](docs/user-guide.md) |
| администрируете Portal / Harbor | [Руководство администратора](docs/admin-guide.md) |
| устанавливаете Portal в закрытом контуре | [Offline installation guide](deploy/offline/README.md) |
| устраняете проблему | [Troubleshooting](docs/troubleshooting.md) |
| разрабатываете или сопровождаете проект | [Локальная разработка Windows/Linux](docs/development.md), [карта документации](docs/README.md), [CONTRIBUTING.md](CONTRIBUTING.md) |

## Как это работает

```text
Harbor SOURCE
    ↓
Portal (SOURCE)
    ↓
подписанный Offline Bundle v1 + `.sha256` + signed handoff
    ↓
разрешённый физический носитель
    ↓
Portal (TARGET)
    ↓
проверка и импорт
    ↓
Harbor TARGET
```

Оператор выбирает точные версии image/chart на SOURCE. Для browser physical handoff он переносит комплект одной delivery: bundle, `.sha256` и подписанный `.htp-handoff.json`; на TARGET handoff проверяется до Bundle v1 preview и разрешённого импорта. Подробный сценарий описан в [пользовательском руководстве](docs/user-guide.md).

## Ключевые гарантии

- SOURCE и TARGET не требуют прямого portal-to-portal или Harbor-to-Harbor соединения.
- Полученный bundle считается недоверенным до успешной проверки на TARGET.
- Bundle v1 использует Ed25519 для подписи и SHA-256 для контроля целостности.
- Конфликт не приводит к неявной перезаписи target artifact.
- Credentials, JWT secrets и signing private keys не включаются в bundle и не должны храниться в Git.

Подробнее: [модель безопасности](docs/security.md) и [Offline Bundle v1](docs/offline-bundle-v1.md).

## Документация

| Тема | Документ |
|---|---|
| Обзор продукта | [Паспорт проекта](docs/project-passport.md) |
| Установка и эксплуатация | [Offline guide](deploy/offline/README.md), [Admin guide](docs/admin-guide.md) |
| Runtime SOURCE/TARGET и ключи | [Runtime mode](docs/runtime-mode.md), [Key management](docs/key-management.md) |
| Архитектура и разработка | [Windows/Linux development](docs/development.md), [docs/README.md](docs/README.md), [Architecture](docs/architecture.md), [CONTRIBUTING.md](CONTRIBUTING.md) |
| Тестирование и CI | [Testing](docs/testing.md) |
| Релиз | [Release notes v1.0.0](docs/release-notes-v1.0.0.md), [CHANGELOG.md](CHANGELOG.md) |

Исторический `docs/harbor-transfer-portal.md` не определяет текущее runtime, protocol или security behavior.

## Лицензия

Проект распространяется по лицензии [Apache License 2.0](LICENSE).