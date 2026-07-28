@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "PYTHONDONTWRITEBYTECODE=1"
set "PROJECT_ROOT=%~dp0.."
set "PYTHON_EXE="

rem Prefer a Python interpreter explicitly supplied by the user.
if defined TEST_PYTHON if exist "%TEST_PYTHON%" set "PYTHON_EXE=%TEST_PYTHON%"

rem Prefer the project's virtual environment when available.
if not defined PYTHON_EXE if exist "%PROJECT_ROOT%\.venv\Scripts\python.exe" set "PYTHON_EXE=%PROJECT_ROOT%\.venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "%PROJECT_ROOT%\venv\Scripts\python.exe" set "PYTHON_EXE=%PROJECT_ROOT%\venv\Scripts\python.exe"

rem Fall back to the first python.exe available on PATH.
if not defined PYTHON_EXE (
    for /f "delims=" %%I in ('where python 2^>nul') do (
        if not defined PYTHON_EXE set "PYTHON_EXE=%%I"
    )
)

if not defined PYTHON_EXE (
    echo ERROR: Python could not be found.
    echo Install Python or create a project virtual environment in .venv or venv.
    pause
    exit /b 2
)

echo Using Python:
echo %PYTHON_EXE%
echo.

rem Ensure pytest exists in the exact interpreter used by the runner.
"%PYTHON_EXE%" -c "import pytest" >nul 2>&1
if errorlevel 1 (
    echo pytest is not installed in this Python environment.
    echo Installing pytest now...
    "%PYTHON_EXE%" -m pip install pytest
    if errorlevel 1 (
        echo.
        echo ERROR: pytest could not be installed automatically.
        echo Run this command manually:
        echo "%PYTHON_EXE%" -m pip install pytest
        echo.
        rem Still create and open a report that explains the setup problem.
        "%PYTHON_EXE%" -B run_tests.py
        set "EXIT_CODE=%ERRORLEVEL%"
        goto OPEN_REPORT
    )
)

"%PYTHON_EXE%" -B run_tests.py
set "EXIT_CODE=%ERRORLEVEL%"

:OPEN_REPORT
echo.
if "%EXIT_CODE%"=="0" (
    echo ALL TESTS PASSED
) else (
    echo ONE OR MORE TESTS DID NOT PASS OR COULD NOT RUN
)

echo.
echo Opening the latest test report...
start "Réunia Test Report" /min "%PYTHON_EXE%" -B "%~dp0serve_report.py"

exit /b %EXIT_CODE%
