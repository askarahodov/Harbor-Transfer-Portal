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

- Git;
- Docker Engine + Docker Compose v2 на Linux или Docker Desktop в режиме **Linux containers** на Windows 10/11;
- доступ к разрешённым Docker build sources для первого source-запуска.

**Python, WSL, GNU Make, Git Bash и PowerShell не требуются** для обычного запуска из исходного репозитория.

### Windows 10/11 — CMD

Откройте обычный `cmd.exe`:

```bat
git clone https://github.com/askarahodov/Harbor-Transfer-Portal.git
cd Harbor-Transfer-Portal
run.cmd
```

### Linux

```bash
git clone https://github.com/askarahodov/Harbor-Transfer-Portal.git
cd Harbor-Transfer-Portal
./run.sh
```

Оба скрипта сохраняют существующий `.env`, при необходимости создают его из `.env.example` и выполняют `docker compose up -d --build --force-recreate`. Секрет JWT при отсутствии значения безопасно генерируется backend и повторно используется из persistent volume; в Git он не попадает. При **первой установке на пустую базу** войдите `admin/admin` и обязательно установите новый пароль от 12 символов. До смены пароля административные и transfer API недоступны. После смены войдите с новым паролем.

Откройте Portal по адресу `http://127.0.0.1:8080/` или локальную документацию `http://127.0.0.1:8080/docs/`. URL рассчитан на локальный bootstrap; для удалённых браузеров нужен одобренный HTTPS-терминатор. Адрес и порт могут быть изменены через `.env`. Изначальный `HARBOR_URL` является placeholder: добавьте действительный локальный Harbor profile через **Настройки**, прежде чем переносить артефакты.

Разработческий Python launcher: `python3 tools/dev.py setup`, `python3 tools/dev.py doctor`, `python3 tools/dev.py up` (Windows: `py -3`). Он необязателен для `run.sh`/`run.cmd`. Подробнее: [docs/development.md](docs/development.md).

### Конфигурация `.env` и Harbor

| Переменная | Описание | Пример |
|---|---|---|
| `PORTAL_CONTOUR` | initial runtime role: `SOURCE` или `TARGET` | `SOURCE` |
| `HARBOR_URL` | Необязательный legacy fallback; настройте локальный Harbor profile через UI | `https://harbor.local.example` |
| `HARBOR_USER` | Необязательный legacy fallback service account | `transfer-bot` |
| `HARBOR_PASSWORD` | Необязательный legacy fallback, предпочтительно file-backed secret или UI | Не храните в Git |
| `JWT_SECRET` | Необязателен: backend генерирует и сохраняет секрет на persistent volume, если значение отсутствует | Не включать в Git |

После bootstrap дополнительные именованные Harbor profiles управляются через **Настройки → Harbor profiles**. Новый Export/Import workflow выбирает нужный enabled profile явно; legacy/default Harbor сохраняется для backward compatibility.

При **первом запуске только с пустой базой** создаётся временный `admin/admin`; до обязательной смены пароля защищённые API-операции запрещены. После смены пароля войдите снова; существующие пользователи при перезапуске не перезаписываются. Не публикуйте bootstrap HTTP вне loopback/TLS терминатора.

### Полезные команды

```text
docker compose ps
docker compose logs -f
docker compose down
docker compose down -v
docker compose restart
docker compose up -d --build --force-recreate
```

`docker compose down -v` удаляет persistent volume и предназначен только для осознанного сброса локальных данных.

### Доступ

- **Web UI:** `http://localhost:8080/`
- **Встроенная документация Docsify:** `http://localhost:8080/docs/`
- **Health:** `http://localhost:8080/api/health`

Стандартный Compose публикует на host только frontend. Backend FastAPI остаётся внутри Compose network, поэтому его встроенный Swagger/OpenAPI UI не является отдельным host endpoint. Путь `/docs/` на опубликованном frontend принадлежит Docsify.

### Встроенная документация

После запуска полная документация доступна через встроенный Docsify на том же host/port:
`http://127.0.0.1:8080/docs/` при стандартном `PORTAL_HTTP_PORT=8080`.

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

Оператор сначала выбирает SOURCE Harbor profile и точные версии image/chart. Для browser physical handoff переносится комплект одной delivery: bundle, `.sha256` и подписанный `.htp-handoff.json`. На TARGET оператор выбирает TARGET Harbor profile, а handoff проверяется до Bundle v1 preview и разрешённого импорта. Подробный сценарий описан в [пользовательском руководстве](docs/user-guide.md).

## Ключевые гарантии

- SOURCE и TARGET не требуют прямого portal-to-portal или Harbor-to-Harbor соединения.
- Полученный bundle считается недоверенным до успешной проверки на TARGET.
- Bundle v1 использует Ed25519 для подписи и SHA-256 для контроля целостности.
- Signed handoff дополнительно связывает физически перенесённые bundle/sidecar с SOURCE identity.
- Конфликт не приводит к неявной перезаписи target artifact.
- Credentials, JWT secrets и signing private keys не включаются в bundle/handoff и не должны храниться в Git.
- Уже созданная transfer operation привязана к immutable Harbor profile snapshot и не «переезжает» на другой registry при изменении Settings.

Подробнее: [модель безопасности](docs/security.md), [Harbor profiles](docs/harbor-profiles.md), [Physical handoff](docs/physical-handoff.md) и [Offline Bundle v1](docs/offline-bundle-v1.md).

## Документация

| Тема | Документ |
|---|---|
| Обзор продукта | [Паспорт проекта](docs/project-passport.md) |
| Пользовательский transfer flow | [User guide](docs/user-guide.md) |
| Установка и эксплуатация | [Offline guide](deploy/offline/README.md), [Admin guide](docs/admin-guide.md) |
| Harbor profiles | [Harbor profiles](docs/harbor-profiles.md), [Settings](docs/settings.md) |
| Runtime SOURCE/TARGET и ключи | [Runtime mode](docs/runtime-mode.md), [Key management](docs/key-management.md) |
| Архитектура и разработка | [Windows/Linux development](docs/development.md), [карта документации](docs/README.md), [Architecture](docs/architecture.md), [CONTRIBUTING.md](CONTRIBUTING.md) |
| Тестирование и CI | [Testing](docs/testing.md) |
| Релиз | [Release notes v1.0.0](docs/release-notes-v1.0.0.md), [CHANGELOG.md](CHANGELOG.md) |

Исторический `docs/harbor-transfer-portal.md` не определяет текущее runtime, protocol или security behavior.

## Лицензия

Проект распространяется по лицензии [Apache License 2.0](LICENSE).
