@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
if errorlevel 1 exit /b 2

set "ACTION=%~1"
if "%ACTION%"=="" set "ACTION=up"
if not "%~2"=="" goto usage
if /I "%ACTION%"=="help" goto help
if /I "%ACTION%"=="-h" goto help
if /I "%ACTION%"=="--help" goto help
if /I "%ACTION%"=="up" goto begin
if /I "%ACTION%"=="build" goto begin
if /I "%ACTION%"=="build-plain" goto begin
if /I "%ACTION%"=="build-clean" goto begin
if /I "%ACTION%"=="start" goto begin
if /I "%ACTION%"=="doctor" goto begin
if /I "%ACTION%"=="status" goto begin
if /I "%ACTION%"=="logs" goto begin
if /I "%ACTION%"=="stop" goto begin
goto usage

:help
echo Harbor Transfer Portal - Windows CMD launcher for Linux containers
echo   run.cmd          First install / rebuild after source update
echo   run.cmd build    Build images only
echo   run.cmd build-plain  Verbose build log for BuildKit/DNS troubleshooting
echo   run.cmd build-clean  No-cache build, keeps containers and data
echo   run.cmd start    Start existing images without building, wait for health
echo   run.cmd doctor   Check Git, Docker Desktop Linux engine and Compose v2
echo   run.cmd status   Show containers and health
echo   run.cmd logs     Follow logs, Ctrl+C to stop watching
echo   run.cmd stop     Stop containers but KEEP persistent data
echo   run.cmd help     Show this help
echo Use a distinct .env, COMPOSE_PROJECT_NAME and PORTAL_VERSION for an RC.
exit /b 0

:usage
echo ERROR: unknown command or too many arguments.
call :help
exit /b 2

:begin
where git >NUL 2>NUL
if errorlevel 1 (
  echo ERROR: Git not found on PATH.
  exit /b 2
)
where docker >NUL 2>NUL
if errorlevel 1 (
  echo ERROR: Docker CLI not found. Install/start Docker Desktop.
  exit /b 2
)
call docker compose version
if errorlevel 1 (
  echo ERROR: Docker Compose v2 required.
  exit /b 2
)
call docker info >NUL 2>NUL
if errorlevel 1 (
  echo ERROR: Docker Engine is not running. Start Docker Desktop.
  exit /b 2
)
set "DOCKER_OS="
for /f "delims=" %%O in ('docker info --format "{{.OSType}}" 2^>NUL') do set "DOCKER_OS=%%O"
if /I not "%DOCKER_OS%"=="linux" (
  echo ERROR: Switch Docker Desktop to Linux containers mode.
  exit /b 2
)
set "PORTAL_VCS_REF="
for /f "delims=" %%R in ('git rev-parse --verify HEAD 2^>NUL') do set "PORTAL_VCS_REF=%%R"
if not defined PORTAL_VCS_REF (
  echo ERROR: Cannot determine Git revision. Use a source checkout.
  exit /b 2
)
echo Docker Linux containers: OK, source revision %PORTAL_VCS_REF%
if /I "%ACTION%"=="doctor" (
  if exist ".env" (echo .env exists; it will be preserved.) else (echo .env missing; first run will create it.)
  exit /b 0
)

if exist ".env\" (
  echo ERROR: .env must be a regular file, not a directory.
  exit /b 2
)
if not exist ".env" (
  if not exist ".env.example" (
    echo ERROR: Missing .env.example
    exit /b 2
  )
  copy /Y ".env.example" ".env" >NUL
  if errorlevel 1 (
    echo ERROR: Could not create .env
    exit /b 2
  )
  echo Created .env from template; existing settings are never overwritten.
)
if /I "%ACTION%"=="up" goto up
if /I "%ACTION%"=="build" goto build_only
if /I "%ACTION%"=="build-plain" goto build_only
if /I "%ACTION%"=="build-clean" goto build_only
if /I "%ACTION%"=="start" goto start_only
if /I "%ACTION%"=="status" goto status
if /I "%ACTION%"=="logs" goto logs
if /I "%ACTION%"=="stop" goto stop
exit /b 2

:up
call :build_images
if errorlevel 1 exit /b 1
goto start_only

:build_only
call :build_images
if errorlevel 1 exit /b 1
echo Build complete. Start with run.cmd start
exit /b 0

:build_images
echo Building backend/frontend images with revision %PORTAL_VCS_REF% ...
if /I "%ACTION%"=="build-plain" (
  call docker compose --progress plain build
) else if /I "%ACTION%"=="build-clean" (
  call docker compose --progress plain build --no-cache
) else (
  call docker compose build
)
if errorlevel 1 (
  echo ERROR: Build failed. Check Docker Desktop disk, DNS/proxy and BuildKit logs.
  echo Retry: run.cmd build-plain for full logs or build-clean for damaged layers.
  echo No containers or volumes were deleted.
  exit /b 1
)
exit /b 0

:start_only
echo Starting prebuilt images without build or pull ...
call docker compose up -d --no-build --pull never --wait --wait-timeout 180
if errorlevel 1 (
  echo ERROR: Startup or health check failed.
  echo Run: run.cmd status, run.cmd logs. For missing images: run.cmd build.
  exit /b 1
)
call docker compose ps
set "DISPLAY_PORT=%PORTAL_HTTP_PORT%"
if not defined DISPLAY_PORT (
  for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
    if /I "%%A"=="PORTAL_HTTP_PORT" set "DISPLAY_PORT=%%B"
  )
)
if not defined DISPLAY_PORT set "DISPLAY_PORT=8080"
echo Portal: http://127.0.0.1:%DISPLAY_PORT%/  Docs: /docs/
echo On a NEW database, login admin/admin and change password immediately.
echo Verify UI revision matches %PORTAL_VCS_REF% ^(start does not rebuild old images^).
exit /b 0

:status
call docker compose ps
exit /b %ERRORLEVEL%

:logs
call docker compose logs -f
exit /b %ERRORLEVEL%

:stop
call docker compose down
if errorlevel 1 exit /b 1
echo Stopped. Persistent volume preserved.
exit /b 0
