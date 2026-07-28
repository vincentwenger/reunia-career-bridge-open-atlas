@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Virtual environment not found. Run install.bat first.
  pause
  exit /b 1
)
call ".venv\Scripts\activate.bat"
start "" http://127.0.0.1:5000
python app.py
pause
