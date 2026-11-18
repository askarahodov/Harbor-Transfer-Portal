# Быстрый запуск Portal на Windows и Linux

**Назначение:** запуск из **Git checkout** на рабочей/тестовой машине, с Docker Compose и доступом к источникам Docker images. На обеих ОС приложение работает **в Linux-контейнерах**. **Python, Node.js, GNU Make и Git Bash на host не требуются.** Docker Desktop может использовать WSL2 как внутренний backend, но пользователю не нужен WSL terminal. В этом режиме данные сохраняются в Docker named volume, а настройки в локальном `.env`.

> **Для закрытого production-контура** используйте предварительно проверенный [offline release kit](../deploy/offline/README.md). Его `install.sh` / backup / restore / upgrade / uninstall — **Linux lifecycle**; Windows-native offline kit пока не квалифицирован. Не выдавайте source checkout за готовый релиз. Nexus из [Draft #503](https://github.com/askarahodov/Harbor-Transfer-Portal/pull/503) ещё не имеет регионального production GO.

## 1. Проверьте необходимое

| Windows 10/11 | Linux |
|---|---|
| Docker Desktop, запущен **Linux containers** engine | Docker Engine и плагин Compose v2 |
| Git в PATH | Git в PATH |
| Доступ к Docker build sources, свободное место на диске | Доступ к Docker build sources, свободное место на диске |

На Windows можно использовать обычный **CMD**; PowerShell подходит для вызова `.\run.cmd`. Native Windows containers не поддерживаются. Docker bridge network уже встроена — не нужно включать host networking или отключать TLS для запуска Portal.

## 2. Первая установка — три команды

**Windows CMD:**

```bat
git clone https://github.com/askarahodov/Harbor-Transfer-Portal.git
cd Harbor-Transfer-Portal
run.cmd
```

**Linux:**

```sh
git clone https://github.com/askarahodov/Harbor-Transfer-Portal.git
cd Harbor-Transfer-Portal
./run.sh
```

Launcher сначала проверяет Git, Docker Engine в Linux mode и Compose v2; копирует `.env.example` в `.env`, **не перезаписывая** существующую конфигурацию; фиксирует текущий `git rev-parse HEAD` в обоих image labels. Затем **отдельно** выполняет Docker build и старт уже собранных images с `--no-build --pull never --wait --wait-timeout 180`. При ошибке сборки контейнеры не удаляются. На успешном старте отображаются статус и ссылка на Portal.

При настройках по умолчанию откройте **http://127.0.0.1:8080/** и **http://127.0.0.1:8080/docs/**. Если база пустая, временно войдите `admin/admin` и **немедленно установите новый пароль длиной не менее 12 символов**: без смены пароля защищённые операции недоступны. Не открывайте bootstrap HTTP во внешнюю сеть; для браузера с удалённого компьютера настройте одобренный HTTPS reverse proxy.

Через **Настройки** задайте только локальные для этого контура Harbor/Nexus profiles, CA, credentials и signing/trust keys. `HARBOR_URL` в `.env.example` — placeholder, не действующий registry.

## 3. Дальнейшие команды

| Что нужно | Windows CMD | Linux |
|---|---|---|
| Проверить Docker/Git/Compose | `run.cmd doctor` | `./run.sh doctor` |
| Собрать новый Git SHA | `run.cmd build` | `./run.sh build` |
| Запустить **без сборки/скачивания** | `run.cmd start` | `./run.sh start` |
| Собрать **и** запустить | `run.cmd` | `./run.sh` |
| Проверить статус | `run.cmd status` | `./run.sh status` |
| Смотреть логи | `run.cmd logs` | `./run.sh logs` |
| Остановить, сохранив данные | `run.cmd stop` | `./run.sh stop` |

`start` **не пересобирает** images. После изменения Git SHA или исходников используйте `build` и `start` либо снова `run.cmd` / `./run.sh`. Сверьте short revision на странице Portal с `git rev-parse HEAD`. Не запускайте `docker compose down -v`: это удаляет named volume с базой, ключами, операциями и настройками.

В качестве необязательного альтернативного CLI для разработчика (при установленном Python 3.12) существуют `python3 tools/dev.py doctor|up|build|start|status|down|logs`, на Windows `py -3 tools/dev.py ...`. CLI пользуется теми же Compose semantics; Python **не нужен** коротким launcher.

## 4. Несколько независимых установок на одной машине

Для SOURCE, TARGET или тестового Nexus RC **не запускайте второй экземпляр с той же конфигурацией**. Каждой установке необходим отдельный каталог исходников и собственные `.env` и volume. Например, после клонирования второго checkout настройте его `.env`:

```dotenv
COMPOSE_PROJECT_NAME=htp-nexus-rc
PORTAL_VERSION=nexus-rc
PORTAL_HTTP_BIND=127.0.0.1
PORTAL_HTTP_PORT=8081
PORTAL_CONTOUR=SOURCE
```

Сохраните остальные параметры шаблона и запускайте `run.cmd` / `./run.sh` **из этого checkout**. `COMPOSE_PROJECT_NAME` задаёт независимый Compose project, включая named volume; `PORTAL_VERSION` задаёт отдельные имена локальных образов, а `PORTAL_HTTP_PORT` исключает конфликт порта. Если эти переменные установлены в командной оболочке, они имеют приоритет над `.env` — проверьте их прежде чем запускать RC. Для настоящих физически изолированных SOURCE/TARGET нужны **разные машины/контуры**, а не просто два Compose project на одном host.

Не переключайте `PORTAL_CONTOUR` в `.env` ради восстановления роли: после bootstrap текущая роль сохраняется в SQLite и меняется штатно через UI. Не переносите приватные SOURCE-ключи на TARGET.

## 5. Если сборка или запуск не удаётся

| Симптом | Что проверить и сделать без потери данных |
|---|---|
| `docker` / Compose не найдены | `run.cmd doctor` или `./run.sh doctor`; установить Docker Desktop/Engine и Compose v2 |
| `docker info` не отвечает | Запустить Docker Desktop / docker daemon; на Windows выбрать **Linux containers** |
| BuildKit завис или не может скачать базовый образ | Выполнить отдельно `run.cmd build` / `./run.sh build`; проверить DNS/proxy, доступ к registry и свободное место Docker. Для подробного вывода: `docker compose build --progress plain`, сохранив **`PORTAL_VCS_REF`**; лучше пользоваться launcher, который выставляет SHA автоматически |
| Образ не найден после `start` | Сперва `build`, затем `start`; `start` специально не вызывает pull/build |
| Frontend показывает `revision: unknown` или старый SHA | Выполнить штатный `build` с launcher и `start`; при прямом `docker compose build` нужно вручную передать текущий `PORTAL_VCS_REF` |
| Порт занят / интерфейс не отвечает | `status`, `logs`; проверить `PORTAL_HTTP_PORT`, `COMPOSE_PROJECT_NAME`, `docker compose config`; не отключать работающий экземпляр на 8080 |
| Health не становится healthy за 180 с | Проверить `docker compose ps`, последние backend/frontend logs, место для volume, migrations, ожидание registry; устранить проблему и повторить `start` |
| `containerd failed to stat parent` | Повреждённый Docker layer/cache на **конкретном host**, не подтверждённый дефект Portal. Сначала изучить Docker Desktop disk / daemon logs. Избегать массового `system prune --volumes`, `down -v` или удаления работающих images/volumes; адресно чинить только изолированный RC после резервной копии |

**Не публикуйте логи с credentials**, весь `.env`, JWT или signing keys. Не отключайте TLS или ACL ради восстановления сборки. См. [Troubleshooting](troubleshooting.md) и [Development](development.md).

## 6. Offline release на Linux

Квалифицированная offline-поставка с внешним SHA-256 отличается от source checkout: оператор переносит versioned kit с checksum, запускает `install.sh` (load уже готовых images без build/pull), использует `backup.sh` / `restore.sh` / `upgrade.sh` и отдельный production approval. [Пошаговая инструкция](../deploy/offline/README.md). Утверждение, что Windows source Docker Desktop работает, **не равно** квалификации Windows-native offline installer.
