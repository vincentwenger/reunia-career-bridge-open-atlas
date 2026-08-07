@echo off
setlocal

python "%~dp0validate_lightsail_deployment.py" %*
set "VALIDATION_EXIT=%ERRORLEVEL%"

if not "%VALIDATION_EXIT%"=="0" (
  echo.
  echo Deployment validation failed. Review the errors above.
)

exit /b %VALIDATION_EXIT%
