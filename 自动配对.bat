@echo off
REM DianXiaoMi SKU Auto Pairing Script - Windows
REM Double-click to start auto pairing

cd /d "%~dp0"

echo ==================================================
echo   DianXiaoMi SKU Auto Pairing
echo ==================================================
echo.

REM Check Python installation
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found
    echo Please install Python 3.9 or higher
    echo Download: https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

REM Check and install dependencies
echo Checking dependencies...
python -c "import playwright" >nul 2>&1
if errorlevel 1 (
    echo First run, installing dependencies...
    pip install playwright pandas openpyxl
    python -m playwright install chromium
    echo.
)

REM Run auto pairing script
echo.
echo Starting auto pairing...
echo.
python scripts/auto_pair_sku.py --max-orders 100

pause
