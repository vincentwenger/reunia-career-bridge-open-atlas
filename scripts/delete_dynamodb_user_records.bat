@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "PYTHON_CMD="

where py >nul 2>nul
if %ERRORLEVEL% EQU 0 set "PYTHON_CMD=py -3"

if not defined PYTHON_CMD (
  where python >nul 2>nul
  if %ERRORLEVEL% EQU 0 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
  echo ERROR: Python 3 was not found on PATH.
  exit /b 1
)

%PYTHON_CMD% "%SCRIPT_DIR%delete_dynamodb_user_records.py" %*
exit /b %ERRORLEVEL%
