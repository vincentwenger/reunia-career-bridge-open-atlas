@echo off
setlocal
if not defined CAREER_BRIDGE_APPLICATIONS_TABLE_NAME set "CAREER_BRIDGE_APPLICATIONS_TABLE_NAME=careerbridge_applications"
if not defined CAREER_BRIDGE_WORKFLOWS_TABLE_NAME set "CAREER_BRIDGE_WORKFLOWS_TABLE_NAME=careerbridge_workflows"
if not defined CAREER_BRIDGE_JOB_DISCOVERY_TABLE_NAME set "CAREER_BRIDGE_JOB_DISCOVERY_TABLE_NAME=careerbridge_job_discovery"
set "CAREER_BRIDGE_DOCUMENTS_BUCKET=reunia-career-bridge-documents-prod-081087819788"
set "PROJECT_DIR=%~dp0..\.."
pushd "%PROJECT_DIR%"
if errorlevel 1 exit /b 1

python scripts\deployment\provision_career_bridge_storage.py ^
  --service-name reunia-career-bridge ^
  --create-missing %*
set "RESULT=%ERRORLEVEL%"

popd
exit /b %RESULT%
pause