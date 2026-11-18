@echo off
setlocal EnableExtensions
cd /d "%~dp0"

where docker >NUL 2>&1
if errorlevel 1 (
  echo ERROR: Docker CLI not found. Install and start Docker Desktop.
  exit /b 1
)
set "DOCKER_OS="
for /f "delims=" %%O in ('docker info --format "{{.OSType}}" 2^>NUL') do set "DOCKER_OS=%%O"
if /I not "%DOCKER_OS%"=="linux" (
  echo ERROR: Docker Engine unavailable or not in Linux containers mode.
  echo Start Docker Desktop and switch to Linux containers, then retry.
  exit /b 1
)
docker compose version >NUL 2>&1
if errorlevel 1 (
  echo ERROR: Docker Compose v2 is required.
  exit /b 1
)
if not exist ".env" (
  if not exist ".env.example" (
    echo ERROR: .env.example not found.
    exit /b 1
  )
  copy /Y ".env.example" ".env" >NUL
  if errorlevel 1 exit /b 1
  echo Created .env from template; backend will persist a random JWT secret.
)
docker compose config -q
if errorlevel 1 (
  echo ERROR: Invalid Compose configuration. Check .env and compose.yaml.
  exit /b 1
)
set "PORTAL_VCS_REF=unknown"
for /f "delims=" %%R in ('git rev-parse HEAD 2^>NUL') do set "PORTAL_VCS_REF=%%R"
set "WAIT_TIMEOUT=180"
if defined PORTAL_INSTALL_TIMEOUT_SECONDS set "WAIT_TIMEOUT=%PORTAL_INSTALL_TIMEOUT_SECONDS%"
docker compose up -d --build --force-recreate --wait --wait-timeout %WAIT_TIMEOUT%
if errorlevel 1 (
  echo ERROR: Portal failed to become healthy. Inspect:
  echo   docker compose ps
  echo   docker compose logs --tail=100
  docker compose ps
  exit /b 1
)
set "HTTP_PORT=8080"
set "HTTP_BIND=127.0.0.1"
for /f "tokens=1,* delims==" %%A in ('findstr /B /C:"PORTAL_HTTP_PORT=" ".env"') do set "HTTP_PORT=%%B"
for /f "tokens=1,* delims==" %%A in ('findstr /B /C:"PORTAL_HTTP_BIND=" ".env"') do set "HTTP_BIND=%%B"
if defined PORTAL_HTTP_PORT set "HTTP_PORT=%PORTAL_HTTP_PORT%"
if defined PORTAL_HTTP_BIND set "HTTP_BIND=%PORTAL_HTTP_BIND%"
echo Portal is healthy. Internal HTTP listener: http://%HTTP_BIND%:%HTTP_PORT%/
if /I not "%HTTP_BIND%"=="127.0.0.1" echo Use an approved HTTPS terminator for remote browser access.
echo New database only: sign in with admin/admin and immediately change the password.
endlocal
