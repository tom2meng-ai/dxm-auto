@echo off
REM DianXiaoMi Login Save Script - Windows
REM Run this first to save login state

cd /d "%~dp0"

echo ==================================================
echo   DianXiaoMi - Save Login State
echo ==================================================
echo.
echo This will open a browser window.
echo Please login to DianXiaoMi manually.
echo After login, the script will save your login state.
echo.

REM Check Python installation
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found
    echo Please install Python 3.9 or higher
    pause
    exit /b 1
)

REM Check and install dependencies
echo Checking dependencies...
python -c "import playwright" >nul 2>&1
if errorlevel 1 (
    echo Installing dependencies...
    pip install playwright pandas openpyxl
    python -m playwright install chromium
    echo.
)

echo.
echo Starting browser for login...
echo.
python scripts/auto_pair_sku.py --save-auth

pause
