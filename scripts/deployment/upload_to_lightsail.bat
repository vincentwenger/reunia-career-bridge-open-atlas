@echo off
setlocal

:: Use your locally configured Docker and AWS credentials. Never store passwords in this file.
set "AWS_REGION=us-west-2"
set "LIGHTSAIL_SERVICE=reunia-career-bridge"
set "IMAGE_NAME=reunia-career-bridge"
set "IMAGE_LABEL=test"
set "REQUIRED_SCALE=1"
set "REQUIRED_COMMAND_OVERRIDE_COUNT=0"
set "PROJECT_DIR=%~dp0..\.."
set "DIRECTORY_PUSHED="

pushd "%PROJECT_DIR%"
if errorlevel 1 (
  set "FAILURE_MESSAGE=Could not open the project directory: %PROJECT_DIR%"
  goto :fail
)
set "DIRECTORY_PUSHED=1"

echo [1/5] Building Docker image %IMAGE_NAME%...
docker build -t %IMAGE_NAME% .
if errorlevel 1 (
  set "FAILURE_MESSAGE=Docker image build failed."
  goto :fail
)

echo [2/5] Pushing image to Lightsail service %LIGHTSAIL_SERVICE%...
aws lightsail push-container-image ^
  --region %AWS_REGION% ^
  --service-name %LIGHTSAIL_SERVICE% ^
  --label %IMAGE_LABEL% ^
  --image %IMAGE_NAME%:latest
if errorlevel 1 (
  set "FAILURE_MESSAGE=Lightsail image push failed."
  goto :fail
)

echo [3/5] Verifying Lightsail uses the image command without an override...
set "COMMAND_OVERRIDE_OUTPUT=%TEMP%\reunia-lightsail-command-%RANDOM%-%RANDOM%.txt"
aws lightsail get-container-services ^
  --region %AWS_REGION% ^
  --service-name %LIGHTSAIL_SERVICE% ^
  --query "length(containerServices[0].currentDeployment.containers.*.command[])" ^
  --output text > "%COMMAND_OVERRIDE_OUTPUT%"
if errorlevel 1 (
  if exist "%COMMAND_OVERRIDE_OUTPUT%" del /q "%COMMAND_OVERRIDE_OUTPUT%" >nul 2>&1
  set "FAILURE_MESSAGE=Could not verify whether the Lightsail deployment overrides the image command."
  goto :fail
)

set "COMMAND_OVERRIDE_COUNT="
set /p "COMMAND_OVERRIDE_COUNT="<"%COMMAND_OVERRIDE_OUTPUT%"
del /q "%COMMAND_OVERRIDE_OUTPUT%" >nul 2>&1

if not "%COMMAND_OVERRIDE_COUNT%"=="%REQUIRED_COMMAND_OVERRIDE_COUNT%" (
  set "FAILURE_MESSAGE=Unsafe Lightsail command override detected. Leave the container Command field empty so the Docker image starts Gunicorn with 1 worker and 4 threads."
  goto :fail
)

echo [4/5] Enforcing Lightsail scale %REQUIRED_SCALE%...
aws lightsail update-container-service ^
  --region %AWS_REGION% ^
  --service-name %LIGHTSAIL_SERVICE% ^
  --scale %REQUIRED_SCALE% >nul
if errorlevel 1 (
  set "FAILURE_MESSAGE=Could not set the Lightsail service scale to %REQUIRED_SCALE%."
  goto :fail
)

echo [5/5] Verifying Lightsail service scale...
set "SCALE_OUTPUT=%TEMP%\reunia-lightsail-scale-%RANDOM%-%RANDOM%.txt"
aws lightsail get-container-services ^
  --region %AWS_REGION% ^
  --service-name %LIGHTSAIL_SERVICE% ^
  --query "containerServices[0].scale" ^
  --output text > "%SCALE_OUTPUT%"
if errorlevel 1 (
  if exist "%SCALE_OUTPUT%" del /q "%SCALE_OUTPUT%" >nul 2>&1
  set "FAILURE_MESSAGE=Could not verify the Lightsail service scale."
  goto :fail
)

set "ACTUAL_SCALE="
set /p "ACTUAL_SCALE="<"%SCALE_OUTPUT%"
del /q "%SCALE_OUTPUT%" >nul 2>&1

if not "%ACTUAL_SCALE%"=="%REQUIRED_SCALE%" (
  set "FAILURE_MESSAGE=Scale verification failed. Expected %REQUIRED_SCALE% but Lightsail returned '%ACTUAL_SCALE%'."
  goto :fail
)

popd
set "DIRECTORY_PUSHED="
echo.
echo SUCCESS: Image pushed, no Lightsail command override detected, and service scale verified as %REQUIRED_SCALE%.
echo Runtime invariant: Gunicorn workers = 1; Gunicorn threads = 4.
echo Public endpoint: port 8000, protocol HTTP; health check /health
echo Next: run scripts\deployment\validate_lightsail_deployment.bat after the deployment is active.
exit /b 0

:fail
if defined DIRECTORY_PUSHED popd
echo.
echo ============================================================
echo ERROR: DEPLOYMENT STOPPED
echo %FAILURE_MESSAGE%
echo Required deployment invariant:
echo   Lightsail scale = 1
echo   Lightsail Command field = empty (use the Docker image CMD)
echo   Gunicorn workers = 1
echo   Gunicorn threads = 4
echo This is required because workflow state is process-local and application
echo records use SQLite.
echo ============================================================
exit /b 1
