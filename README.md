# Harbor Transfer Portal

![Harbor Transfer Portal](docs/img/devops-logo-white.png)

[![CI](https://github.com/askarahodov/Harbor-Transfer-Portal/actions/workflows/ci.yml/badge.svg)](https://github.com/askarahodov/Harbor-Transfer-Portal/actions/workflows/ci.yml)

**Harbor Transfer Portal** — локальный веб-портал для безопасной офлайн-передачи container images и Helm OCI charts между физически и сетево изолированными Harbor-контурами.

Портал используется, когда SOURCE и TARGET не имеют прямого сетевого соединения: пакет формируется на SOURCE, переносится разрешённым физическим способом и проверяется перед импортом на TARGET.

> **Статус:** функциональный объём **v1.0.0** реализован и прошёл release qualification. Production rollout требует локального change/release approval и проверки инфраструктуры конкретного контура.

## С чего начать

| Ваша роль | Куда идти |
|---|---|
| Оператор переноса | [Пользовательское руководство](docs/user-guide.md) |
| Администратор Portal / Harbor | [Руководство администратора](docs/admin-guide.md) |
| Установка в закрытом контуре | [Offline installation guide](deploy/offline/README.md) |
| Диагностика проблем | [Troubleshooting](docs/troubleshooting.md) |
| Обзор продукта | [Паспорт проекта](docs/project-passport.md) |
| Разработка и сопровождение | [Карта документации](docs/README.md) и [CONTRIBUTING.md](CONTRIBUTING.md) |

Для штатного переноса оператору не нужно вручную работать с `skopeo`, `helm`, `tar`, `sha256sum` или Harbor CLI.

## Как проходит перенос

```text
Harbor SOURCE
    ↓
Portal (SOURCE)
    ↓
подписанный Offline Bundle v1 + .sha256
    ↓
разрешённый физический носитель
    ↓
Portal (TARGET)
    ↓
проверка и импорт
    ↓
Harbor TARGET
```

Коротко для оператора:

1. На SOURCE выбрать точные версии image/chart и сформировать bundle.
2. Перенести bundle и `.sha256` вместе по принятой в организации процедуре.
3. На TARGET дождаться проверки, просмотреть результат и выполнить разрешённый импорт.

Подробный сценарий, роли, состояния и пользовательские ошибки описаны в [docs/user-guide.md](docs/user-guide.md).

## Ключевые гарантии

- SOURCE и TARGET не требуют прямого portal-to-portal или Harbor-to-Harbor соединения.
- Полученный bundle считается недоверенным до успешной проверки на TARGET.
- Bundle v1 использует цифровую подпись Ed25519 и контроль целостности SHA-256.
- Конфликт не приводит к неявной перезаписи target artifact.
- Credentials, JWT secrets и signing private keys не включаются в bundle и не должны храниться в Git.

Полная модель угроз и доверия: [docs/security.md](docs/security.md). Формат пакета: [docs/offline-bundle-v1.md](docs/offline-bundle-v1.md).

## Установка

Для production/offline deployment используйте versioned offline installation kit с заранее собранными images, без online build/pull в закрытом контуре.

- [Offline installation guide](deploy/offline/README.md)
- [Руководство администратора](docs/admin-guide.md)
- [Runtime SOURCE/TARGET mode](docs/runtime-mode.md)
- [Управление ключами](docs/key-management.md)

## Разработка

Архитектура, локальная разработка, проверки и правила изменений вынесены из README в профильные документы:

- [docs/README.md](docs/README.md) — карта документации и приоритет источников;
- [CONTRIBUTING.md](CONTRIBUTING.md) — правила разработки и проверок;
- [docs/architecture.md](docs/architecture.md) — текущая архитектура;
- [docs/testing.md](docs/testing.md) — тестовая стратегия и CI;
- [deploy/README.md](deploy/README.md) — development/runtime deployment.

Исторический `docs/harbor-transfer-portal.md` не определяет текущее runtime, protocol или security behavior.

## Release

- [Release notes v1.0.0](docs/release-notes-v1.0.0.md)
- [CHANGELOG.md](CHANGELOG.md)

## Лицензия

Проект распространяется по лицензии [Apache License 2.0](LICENSE).
