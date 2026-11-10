@echo off
setlocal
cd /d "%~dp0"
if not exist ".env" (
  copy /Y ".env.example" ".env" >NUL
  if errorlevel 1 exit /b 1
  echo .env создан из шаблона. JWT_SECRET сохранит backend в persistent volume.
)
docker compose up -d --build --force-recreate
if errorlevel 1 exit /b 1
echo Portal: http://127.0.0.1:8080/
echo На новой базе войдите admin/admin и обязательно смените пароль.
endlocal
