# Разработка и локальный запуск на Windows и Linux

**Статус:** актуальный developer workflow для source checkout.

Harbor Transfer Portal использует одинаковые Linux container images на обеих поддерживаемых host OS:

- Linux — Docker Engine + Docker Compose v2;
- Windows 10/11 — Docker Desktop в режиме **Linux containers**.

Application runtime использует обычную Compose bridge network и service discovery; включать Docker Desktop **host networking** не требуется. На host публикуется только frontend port, backend остаётся внутри Compose network.

Для source build/run Windows **не требует** WSL, Git Bash или GNU Make. Общая orchestration реализована в `tools/dev.py` и использует только Python standard library и argv-based subprocess calls.

## Предпосылки

На обеих ОС:

- Git;
- Python 3.12;
- Docker с Compose v2;
- доступ к разрешённым build sources для Docker build.

Для изменения frontend вне Docker дополнительно нужен Node.js 22.

Создайте локальную конфигурацию из `.env.example`. Реальные secrets в Git не добавляются.

### Linux

```bash
cp .env.example .env
python3 tools/dev.py doctor
python3 tools/dev.py build
python3 tools/dev.py up
```

### Windows PowerShell

```powershell
Copy-Item .env.example .env
.\dev.ps1 doctor
.\dev.ps1 build
.\dev.ps1 up
```

Эквивалент без PowerShell wrapper:

```powershell
py -3 tools/dev.py doctor
py -3 tools/dev.py build
py -3 tools/dev.py up
```

## Команды

| Команда | Назначение |
|---|---|
| `doctor` | проверяет Git, Docker, Compose и наличие `.env` |
| `build` | собирает backend/frontend Docker images из текущего Git revision |
| `up` | выполняет build и `--force-recreate` локального stack |
| `down` | останавливает stack без удаления persistent volume |
| `logs` | показывает Compose logs |
| `compose-config` | валидирует итоговую Compose configuration |

`build` и `up` автоматически передают `PORTAL_VCS_REF=<git HEAD>`. Frontend показывает короткий `UI <revision>` в верхней панели, поэтому после обновления source можно проверить, что браузер действительно обслуживает новую сборку.

Для Linux команды `make up`, `make down`, `make logs` и `make compose-config` остаются convenience aliases и делегируют ту же логику `tools/dev.py`.

## Обновление source checkout

Linux:

```bash
git pull
python3 tools/dev.py up
```

Windows PowerShell:

```powershell
git pull
.\dev.ps1 up
```

Обе команды пересобирают images и force-recreate контейнеры. Простой restart старого container не считается обновлением source build.

## Что именно поддерживается на Windows

Поддерживаемый Windows developer path — Docker Desktop с Linux containers. Backend, Skopeo, Helm и Nginx выполняются внутри тех же Linux images, что и на Linux host. Это уменьшает platform drift.

Frontend source build можно выполнять native на Windows после `npm ci`:

```powershell
Set-Location frontend
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
doctor
docker compose version
UI <revision>
```

На Windows Docker Desktop должен быть переключён в Linux containers mode. Если `docker compose build` пытается использовать Windows containers, переключите Docker Desktop в Linux containers и повторите `doctor` / `build`.
