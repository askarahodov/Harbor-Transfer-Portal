# Установка Harbor Transfer Portal из готовых интернет-образов

Этот способ **скачивает готовые образы** из GitHub Container Registry (GHCR). На рабочей Windows/Linux машине **нет Docker build**, Python, Node.js, Git Bash или WSL не требуются. Нужны Git, Docker с Compose v2 и доступ к GHCR.

> Важно: пока CI не опубликует образы для коммита `main`, запуск выдаст ошибку скачивания. Наличие `compose.online.yaml` само по себе не означает, что пакеты уже опубликованы. GHCR package по умолчанию может быть приватным.

## Публикация образов

После **успешного CI от push в main** workflow `.github/workflows/publish-images.yml` собирает и публикует два Linux/AMD64 образа:

```text
ghcr.io/askarahodov/harbor-transfer-portal-backend:sha-<40-character-commit-sha>
ghcr.io/askarahodov/harbor-transfer-portal-frontend:sha-<40-character-commit-sha>
```

Оба образа используют один и тот же Git SHA, Dockerfile содержатся в том же проверенном коммите. Публикация по pull request **не выполняется**. Сначала дождитесь завершения [CI](https://github.com/askarahodov/Harbor-Transfer-Portal/actions/workflows/ci.yml), затем [Publish prebuilt images to GHCR](https://github.com/askarahodov/Harbor-Transfer-Portal/actions/workflows/publish-images.yml) для соответствующего `main` SHA.

Доступ регулируется видимостью **обоих** пакетов в GHCR. Для скачивания без авторизации владелец должен отдельно сделать оба пакета публичными через GitHub package settings. Для приватных пакетов предварительно выполните `docker login ghcr.io` с правом `read:packages`; пароли и PAT не записывайте в `.env` или Git. Публикация **не делает пакеты публичными автоматически**.

На первом этапе публикуется только `linux/amd64` — это Linux-хосты x86-64 и Windows Docker Desktop в режиме Linux containers на x86-64. ARM64 пока не заявлен.

## Чистый запуск Windows

Откройте `cmd.exe` с работающим Docker Desktop в режиме Linux containers:

```bat
git clone https://github.com/askarahodov/Harbor-Transfer-Portal.git
cd Harbor-Transfer-Portal
run-online.cmd
```

## Чистый запуск Linux

```sh
git clone https://github.com/askarahodov/Harbor-Transfer-Portal.git
cd Harbor-Transfer-Portal
./run-online.sh
```

Скрипты создают `.env` из `.env.example` только если файла ещё нет, определяют Git SHA выбранного checkout, проверяют Docker и Compose, выполняют:

```text
docker compose -f compose.online.yaml pull --policy always
docker compose -f compose.online.yaml up -d --no-build --pull never --wait --wait-timeout 180
```

При ошибке `pull` запуск прекращается **до** изменения контейнеров. Если образ ещё не опубликован, дождитесь успешной публикации конкретного SHA или используйте уже опубликованный checkout; не подменяйте тег случайной версией и не запускайте `run.cmd` как workaround (он пересобирает образы локально).

После запуска доступны `http://127.0.0.1:8080/` и `/api/health`. На совершенно новой базе войдите `admin/admin` и немедленно смените пароль. Для удалённого доступа требуется HTTPS-терминатор.

## Изоляция данных и портов

`compose.online.yaml` имеет отдельное имя проекта **`harbor-transfer-portal-online`**, а значит и отдельный named volume `portal-data`. Он **не переиспользует** данные исходного `compose.yaml` или offline installation. Это намеренно: новая установка не должна менять уже работающие контейнеры и базу. Для управляемой миграции нужен отдельный план резервного копирования и восстановления.

Если порт 8080 занят, после создания `.env` задайте `PORTAL_HTTP_PORT=8081` и повторите launcher. **Не останавливайте и не удаляйте существующий Portal.** При нескольких online-установках на одном Docker host задайте каждой уникальный `COMPOSE_PROJECT_NAME` и порт. Не используйте `docker compose down -v`, если хотите сохранить состояние.

Для диагностики используйте:

```sh
docker compose -f compose.online.yaml ps
docker compose -f compose.online.yaml logs --tail=100
```

Для диагностики вручную из shell нужно сначала передать в среду `PORTAL_IMAGE_REVISION`, равный `git rev-parse HEAD`, поскольку онлайн-конфигурация намеренно требует явного SHA. На Windows `cmd.exe` его можно получить через `for /f`, но проще повторно выполнить `run-online.cmd`.

Этот online path **не является offline kit**, не включает lifecycle install/upgrade/restore из `deploy/offline` и требует интернет-доступа при каждом новом скачивании образов. Сохранённые Docker images можно повторно использовать только при совпадении SHA и архитектуры; launcher выполняет явный pull для проверки соответствия опубликованным артефактам.
