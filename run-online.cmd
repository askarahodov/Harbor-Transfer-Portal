@echo off
setlocal
cd /d "%~dp0"

where docker >NUL 2>NUL
if errorlevel 1 (
  echo ERROR: Docker не найден. Установите Docker Desktop. 1>&2
  exit /b 1
)
call docker info >NUL 2>NUL
if errorlevel 1 (
  echo ERROR: Docker Engine недоступен. Запустите Docker Desktop. 1>&2
  exit /b 1
)
call docker compose version >NUL 2>NUL
if errorlevel 1 (
  echo ERROR: Docker Compose v2 недоступен. 1>&2
  exit /b 1
)
set "PORTAL_DOCKER_OS="
for /f "usebackq delims=" %%O in (`docker info --format "{{.OSType}}" 2^>NUL`) do set "PORTAL_DOCKER_OS=%%O"
if /I not "%PORTAL_DOCKER_OS%"=="linux" (
  echo ERROR: Требуется Docker Desktop в режиме Linux containers ^(OSType=%PORTAL_DOCKER_OS%^). 1>&2
  exit /b 1
)

if not exist ".env" (
  if not exist ".env.example" (
    echo ERROR: Не найден .env.example. 1>&2
    exit /b 1
  )
  copy /Y ".env.example" ".env" >NUL
  if errorlevel 1 exit /b 1
  echo .env создан из шаблона. JWT_SECRET сохранит backend в persistent volume.
)
set "PORTAL_IMAGE_REVISION="
for /f "usebackq delims=" %%R in (`git rev-parse --verify HEAD 2^>NUL`) do set "PORTAL_IMAGE_REVISION=%%R"
if not defined PORTAL_IMAGE_REVISION (
  echo ERROR: Не удалось определить Git commit. Запустите из клона опубликованного main. 1>&2
  exit /b 1
)

call docker compose -f compose.online.yaml config -q
if errorlevel 1 (
  echo ERROR: Ошибка конфигурации Compose или .env. 1>&2
  exit /b 1
)
call docker compose -f compose.online.yaml pull --policy missing
if errorlevel 1 (
  echo ERROR: Образы недоступны в GHCR. Проверьте публикацию для этого Git commit, сеть и права доступа ^(docker login ghcr.io^). 1>&2
  exit /b 1
)
call docker compose -f compose.online.yaml up -d --no-build --pull never --wait --wait-timeout 180
if errorlevel 1 (
  echo ERROR: Портал не готов. Проверьте docker compose -f compose.online.yaml ps и docker compose -f compose.online.yaml logs --tail=100. 1>&2
  exit /b 1
)
echo Portal готов из готовых GHCR образов. Опубликованный адрес ^(host:port^):
call docker compose -f compose.online.yaml port frontend 8080
if errorlevel 1 exit /b 1
echo На новой базе войдите admin/admin и обязательно смените пароль.
endlocal
