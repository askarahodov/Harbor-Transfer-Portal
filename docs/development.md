# Разработка и локальный запуск на Windows и Linux

**Статус:** актуальный cross-platform source launcher. `run.sh`/`run.cmd` запускают Portal без Python на host; `tools/dev.py` — необязательный developer CLI на Python 3.12.

Harbor Transfer Portal использует одинаковые Linux container images на обеих поддерживаемых host OS:

- Linux — Docker Engine + Docker Compose v2;
- Windows 10/11 — Docker Desktop в режиме **Linux containers**.

Application runtime использует обычную Compose bridge network и service discovery; включать Docker Desktop **host networking** не требуется. На host публикуется только frontend port, backend остаётся внутри Compose network.

Для source build/run Windows **не требует** WSL, Git Bash или GNU Make. Общая orchestration реализована в `tools/dev.py` и использует только Python standard library и argv-based subprocess calls.

## Предпосылки

На обеих ОС:

- Git;
- Docker с Compose v2;
- доступ к разрешённым build sources для Docker build.

Для Python developer CLI дополнительно нужен Python 3.12. Для изменения frontend вне Docker нужен Node.js 22.

Скрипты создают `.env` из `.env.example`, только если его ещё нет, и сохраняют существующие настройки при повторном старте. Реальные secrets в Git не добавляются. На пустой базе доступен временный вход `admin/admin`: смена пароля от 12 символов обязательна, старые токены отзываются. `run.sh`/`run.cmd` используют генерацию JWT_SECRET в backend persistent volume; `tools/dev.py setup` дополнительно может записать локальный секрет непосредственно в `.env`.

### Linux

Быстрый старт без Python на хосте:

```bash
./run.sh
```

Необязательный developer CLI (Python 3.12):

```bash
python3 tools/dev.py setup
python3 tools/dev.py doctor
python3 tools/dev.py up
```

### Windows CMD — canonical path

Быстрый старт в обычном `cmd.exe`, без PowerShell и WSL:

```bat
run.cmd
```

Необязательный developer CLI (если установлен Python 3.12):

```bat
py -3 tools/dev.py setup
py -3 tools/dev.py doctor
py -3 tools/dev.py up
```

Оба коротких скрипта проверяют доступность Docker Engine, Compose v2 и Linux containers mode, сохраняют существующий `.env`, при необходимости создают его из `.env.example`, валидируют Compose config, затем выполняют двухфазный запуск: `docker compose build` с точным Git revision и `docker compose up -d --no-build --pull never --wait --wait-timeout 180`. Успех объявляется только после прохождения healthcheck обоих сервисов. Адрес печатается после успешного healthcheck; фактический host/port определяется через `docker compose port frontend 8080`. При ошибке смотрите `docker compose ps` и `docker compose logs --tail=100`. На повторном запуске без пересборки используйте `run.cmd start` / `./run.sh start`; для диагностики сборки — `build-plain` и `build-clean`, для проверки — `doctor`, `status`, `logs`, для безопасной остановки — `stop`. Запуск на Windows требует Docker Desktop в Linux containers mode. `tools/dev.py` не зависит от shell-specific features. Подробный сценарий: [быстрый запуск Windows/Linux](install-windows-linux.md).

### Windows PowerShell — optional convenience

Если PowerShell scripts разрешены, `dev.ps1` остаётся тонким convenience wrapper над тем
же `tools/dev.py`:

```powershell
.\dev.ps1 doctor
.\dev.ps1 build
.\dev.ps1 up
```

Использование wrapper не даёт отдельного runtime behavior и не является обязательным
условием поддержки Windows.

## Команды

| Команда | Назначение |
|---|---|
| `setup` | идемпотентно создаёт `.env` и локальный `JWT_SECRET`, ничего не перезаписывая |
| `bootstrap` | явно проверяет временный локальный вход `admin/admin` (не выполняется автоматически при каждом запуске) |
| `doctor` | проверяет Git, Docker, Compose и состояние `.env`, объясняет fallback JWT в volume |
| `build` | собирает backend/frontend Docker images из текущего Git revision |
| `up` | автоматически создаёт `.env` при отсутствии, собирает images и запускает их отдельным `--no-build --pull never` шагом с ожиданием healthy |
| `start` | запускает имеющиеся images с `--no-build --pull never`; не собирает новый SHA |
| `status` | показывает состояние Compose services |
| `down` | останавливает stack без удаления persistent volume |
| `logs` | показывает Compose logs |
| `compose-config` | валидирует итоговую Compose configuration |

`build` и `up` автоматически передают `PORTAL_VCS_REF=<git HEAD>`. Frontend показывает короткий `UI <revision>` в верхней панели, поэтому после обновления source можно проверить, что браузер действительно обслуживает новую сборку.

Для Linux команды `make up`, `make start`, `make status`, `make down`, `make logs` и `make compose-config` делегируют ту же логику `tools/dev.py`.

## Обновление source checkout

Linux:

```bash
git pull
python3 tools/dev.py up
```

Windows CMD:

```bat
git pull
py -3 tools/dev.py up
```

При разрешённом PowerShell тот же шаг можно выполнить через optional `.\dev.ps1 up`.
Обе формы пересобирают images и запускают существующий Compose project с ожиданием health. Docker Compose пересоздаёт нужные сервисы при изменении образов, но не принуждает пересоздавать всё при каждом запуске. Простой `start` без сборки **не обновляет** source revision; сверяйте ревизию в UI.

## Что именно поддерживается на Windows

Поддерживаемый Windows developer path — Docker Desktop с Linux containers. Backend, Skopeo, Helm и Nginx выполняются внутри тех же Linux images, что и на Linux host. Это уменьшает platform drift.

Frontend source build можно выполнять native на Windows после `npm ci`:

```bat
cd frontend
npm ci --no-audit --no-fund
npm run build
```

Полный native backend virtualenv из `backend/requirements-dev.lock` **не является Windows contract**: lock содержит Linux-oriented runtime dependencies. Для Windows backend build/run используйте Docker path выше.

## Offline release boundary

Versioned offline kit и его lifecycle scripts (`install.sh`, `backup.sh`, `restore.sh`, `upgrade.sh`) являются Linux runtime tooling. Windows может использоваться как developer host для source Docker build/run, но native Windows offline installation не заявляется как поддерживаемая без отдельного qualification contract.

Это ограничение намеренное: документация не должна выдавать POSIX lifecycle scripts за Windows-native поддержку.

## Диагностика

Проверьте:

```text
run.cmd doctor / ./run.sh doctor
run.cmd status / ./run.sh status
run.cmd logs / ./run.sh logs
UI <revision>
```

При проблемах BuildKit сначала выполните сборку отдельно (`build`), разберите DNS/proxy/disk, затем запустите готовые images через `start`; штатный launcher гарантирует `PORTAL_VCS_REF`. Для двух экземпляров используйте независимые `COMPOSE_PROJECT_NAME`, `PORTAL_VERSION`, `PORTAL_HTTP_PORT` и volumes — [пример](install-windows-linux.md).

На Windows Docker Desktop должен быть переключён в Linux containers mode. `run.cmd` считывает `docker info --format "{{.OSType}}"` без `findstr`, чтобы корректно принимать вывод `linux` с Windows CRLF. `.gitattributes` принудительно сохраняет LF у `*.sh` и Dockerfile, а Docker images дополнительно нормализуют скопированные entrypoint scripts на случай старого Windows checkout с CRLF. После обновления этих файлов для проверки используйте повторную сборку через `run.cmd`. Не переносите в Git локальные исправления Dockerfile и не используйте `down -v`: это удалит данные. `doctor` проверяет `docker info` и завершится ошибкой, если Docker сообщает другой `OSType`; после переключения повторите `doctor` / `build`.

При проблемах BuildKit сначала выполните сборку отдельно (`build`), разберите DNS/proxy/disk, затем запустите готовые images через `start`; штатный launcher гарантирует `PORTAL_VCS_REF`. Для двух экземпляров используйте независимые `COMPOSE_PROJECT_NAME`, `PORTAL_VERSION`, `PORTAL_HTTP_PORT` и volumes — [пример](install-windows-linux.md).
