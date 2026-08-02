@echo off
setlocal
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