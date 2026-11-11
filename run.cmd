@echo off
setlocal
cd /d "%~dp0"
if not exist ".env" (
  copy /Y ".env.example" ".env" >NUL
  if errorlevel 1 exit /b 1
  echo .env создан из шаблона. JWT_SECRET сохранит backend в persistent volume.
)
set "PORTAL_VCS_REF=unknown"
for /f "usebackq delims=" %%R in (`git rev-parse HEAD 2^>NUL`) do set "PORTAL_VCS_REF=%%R"
docker compose up -d --build --force-recreate
if errorlevel 1 exit /b 1
echo Portal: http://127.0.0.1:8080/
echo На новой базе войдите admin/admin и обязательно смените пароль.
endlocal
