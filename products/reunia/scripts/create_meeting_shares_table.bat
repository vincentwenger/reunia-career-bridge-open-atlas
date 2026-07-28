@echo off
setlocal
cd /d "%~dp0\.."
python scripts\create_meeting_shares_table.py
if errorlevel 1 (
  echo.
  echo Failed to create or configure the Meeting Shares DynamoDB table.
  exit /b 1
)
echo.
echo Meeting Shares DynamoDB table is ready.
endlocal