@echo off
cd /d "%~dp0\.."
python scripts\create_admin_analytics_table.py
pause
