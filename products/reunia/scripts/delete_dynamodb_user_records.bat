@echo off
setlocal
cd /d "%~dp0\.."

set "cleanup_interactive="
if "%~1"=="" set "cleanup_interactive=1"

python scripts\delete_dynamodb_user_records.py %*
set "cleanup_exit_code=%errorlevel%"

if defined cleanup_interactive pause
endlocal & exit /b %cleanup_exit_code%
pause