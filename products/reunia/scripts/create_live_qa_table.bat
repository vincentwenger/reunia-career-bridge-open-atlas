@echo off
setlocal
cd /d "%~dp0\.."
python scripts\create_live_qa_table.py
if errorlevel 1 (
  echo.
  echo Failed to create or configure the Live Q^&A DynamoDB table.
  exit /b 1
)
echo.
echo Live Q^&A DynamoDB table is ready.
endlocal
