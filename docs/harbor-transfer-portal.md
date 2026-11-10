# Исходный проектный документ — исторический архив

> **Не инструкция к запуску и не источник текущих требований.** Этот файл ранее содержал ~1300 строк первоначальной постановки, ASCII-макетов, цветовых схем, генератора каркаса и устаревших команд Docker/Helm. Такие примеры могут ошибочно восприниматься как работающий Portal, поэтому они исключены из активной документации.

Полный оригинал сохранён в [истории Git на коммите `2b5779df`](https://github.com/askarahodov/Harbor-Transfer-Portal/blob/2b5779dffb5bbdfab99cf611a6433dd0836f87ab/docs/harbor-transfer-portal.md). Содержимое доступно для исторического/дизайн-анализа, но **не** для развёртывания, формирования bundle или настройки security.

| Вместо первоначального раздела | Текущий источник |
| --- | --- |
| Продукт и роли | [Паспорт проекта](project-passport.md) |
| Архитектура и API | [Архитектура](architecture.md), [контракт Offline Bundle v1](offline-bundle-v1.md) |
| Дизайн-система и frontend | [Frontend](frontend.md), [ADR-008](adr/ADR-008-frontend-ui-kit.md) |
| Установка | [Development](development.md), [offline install](../deploy/offline/README.md) |
| Операторские инструкции | [User guide](user-guide.md), [Admin guide](admin-guide.md) |
| Планы реализации и статус | [GitHub Issues](https://github.com/askarahodov/Harbor-Transfer-Portal/issues) и [ROADMAP](../ROADMAP.md) |

Поток SOURCE → физический подписанный комплект → TARGET реализован иначе, чем в старых примерах. Не копируйте старые shell-скрипты и пример manifest из исторической версии.
