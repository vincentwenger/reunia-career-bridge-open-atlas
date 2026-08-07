@echo off
setlocal EnableExtensions EnableDelayedExpansion

:: Use your locally configured Docker and AWS credentials. Never store passwords in this file.
set "AWS_REGION=us-west-2"

:: Reuse the existing Career Bridge AWS resources unless the caller explicitly
:: overrides one of these values before starting this batch file.
if not defined CAREER_BRIDGE_APPLICATIONS_TABLE_NAME set "CAREER_BRIDGE_APPLICATIONS_TABLE_NAME=careerbridge_applications"
if not defined CAREER_BRIDGE_WORKFLOWS_TABLE_NAME set "CAREER_BRIDGE_WORKFLOWS_TABLE_NAME=careerbridge_workflows"
if not defined CAREER_BRIDGE_JOB_DISCOVERY_TABLE_NAME set "CAREER_BRIDGE_JOB_DISCOVERY_TABLE_NAME=careerbridge_job_discovery"
if not defined CAREER_BRIDGE_DOCUMENTS_BUCKET set "CAREER_BRIDGE_DOCUMENTS_BUCKET=reunia-career-bridge-documents-prod-081087819788"
set "LIGHTSAIL_SERVICE=reunia-career-bridge"
set "IMAGE_NAME=reunia-career-bridge"
set "IMAGE_LABEL=test"
set "REQUIRED_COMMAND_OVERRIDE_COUNT=0"
set "PROJECT_DIR=%~dp0..\.."
set "DIRECTORY_PUSHED="
set "DEMO_STORAGE=0"
set "STORAGE_PREFLIGHT_MODE="

if /I "%CAREER_BRIDGE_ALLOW_DEMO_STORAGE_IN_PRODUCTION%"=="1" set "DEMO_STORAGE=1"
if /I "%CAREER_BRIDGE_ALLOW_DEMO_STORAGE_IN_PRODUCTION%"=="true" set "DEMO_STORAGE=1"
if /I "%CAREER_BRIDGE_ALLOW_DEMO_STORAGE_IN_PRODUCTION%"=="yes" set "DEMO_STORAGE=1"
if /I "%CAREER_BRIDGE_ALLOW_DEMO_STORAGE_IN_PRODUCTION%"=="on" set "DEMO_STORAGE=1"
if "%DEMO_STORAGE%"=="1" set "STORAGE_PREFLIGHT_MODE=--applications-only"

pushd "%PROJECT_DIR%"
if errorlevel 1 (
  set "FAILURE_MESSAGE=Could not open the project directory: %PROJECT_DIR%"
  goto :fail
)
set "DIRECTORY_PUSHED=1"

echo Career Bridge AWS storage configuration:
echo   Applications table: %CAREER_BRIDGE_APPLICATIONS_TABLE_NAME%
echo   Workflows table: %CAREER_BRIDGE_WORKFLOWS_TABLE_NAME%
echo   Job discovery table: %CAREER_BRIDGE_JOB_DISCOVERY_TABLE_NAME%
echo   Documents bucket: %CAREER_BRIDGE_DOCUMENTS_BUCKET%
echo [1/6] Verifying and provisioning Career Bridge AWS storage...
python scripts\deployment\provision_career_bridge_storage.py ^
  --region %AWS_REGION% ^
  --service-name %LIGHTSAIL_SERVICE% ^
  --create-missing %STORAGE_PREFLIGHT_MODE%
if errorlevel 1 (
  set "FAILURE_MESSAGE=Career Bridge DynamoDB/S3 storage preflight failed."
  goto :fail
)

echo [2/6] Building Docker image %IMAGE_NAME%...
docker build -t %IMAGE_NAME% .
if errorlevel 1 (
  set "FAILURE_MESSAGE=Docker image build failed."
  goto :fail
)

echo [3/6] Pushing image to Lightsail service %LIGHTSAIL_SERVICE%...
aws lightsail push-container-image ^
  --region %AWS_REGION% ^
  --service-name %LIGHTSAIL_SERVICE% ^
  --label %IMAGE_LABEL% ^
  --image %IMAGE_NAME%:latest
if errorlevel 1 (
  set "FAILURE_MESSAGE=Lightsail image push failed."
  goto :fail
)

echo [4/6] Verifying Lightsail uses the image command without an override...
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
  set "FAILURE_MESSAGE=Unsafe Lightsail command override detected. Leave the container Command field empty so the versioned Docker image controls Gunicorn startup."
  goto :fail
)

if "%DEMO_STORAGE%"=="1" (
  echo [5/6] Non-durable storage override detected; enforcing Lightsail scale 1...
  aws lightsail update-container-service ^
    --region %AWS_REGION% ^
    --service-name %LIGHTSAIL_SERVICE% ^
    --scale 1 >nul
  if errorlevel 1 (
    set "FAILURE_MESSAGE=Could not set the demo deployment Lightsail service scale to 1."
    goto :fail
  )
) else (
  echo [5/6] Persistent storage mode; preserving the configured Lightsail scale...
)

echo [6/6] Reading current Lightsail service scale...
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

if "%DEMO_STORAGE%"=="1" if not "%ACTUAL_SCALE%"=="1" (
  set "FAILURE_MESSAGE=Non-durable-storage scale verification failed. Expected 1 but Lightsail returned '%ACTUAL_SCALE%'."
  goto :fail
)

popd
set "DIRECTORY_PUSHED="
echo.
echo SUCCESS: Image pushed and no Lightsail command override was detected.
if "%DEMO_STORAGE%"=="1" (
  echo Demo runtime invariant: Lightsail scale = 1; Gunicorn workers = 1.
) else (
  echo Persistent storage mode: current Lightsail scale = %ACTUAL_SCALE%.
  echo Multiple nodes and Gunicorn workers are permitted after successful validation.
)
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
echo   Lightsail Command field = empty (use the Docker image CMD)
if "%DEMO_STORAGE%"=="1" (
  echo   Non-durable storage requires Lightsail scale = 1
  echo   Non-durable storage requires Gunicorn workers = 1
) else (
  echo   Persistent storage requires DynamoDB application/workflow stores and S3 documents
)
echo ============================================================
exit /b 1
