@echo off
REM DianXiaoMi SKU Generator - Windows Start Script
REM Double-click to start the web service

cd /d "%~dp0"

echo =======================================
echo   DianXiaoMi SKU Generator
echo =======================================
echo.
echo Starting service...
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
python -c "import flask, pandas, openpyxl" >nul 2>&1
if errorlevel 1 (
    echo First run, installing dependencies...
    pip install flask pandas openpyxl
    echo.
)

REM Start service
echo.
echo Service started successfully!
echo.
echo Access URL: http://localhost:8080
echo.
echo Tips:
echo   - Browser will open automatically
echo   - If not, please visit the URL above manually
echo   - Closing this window will stop the service
echo.
echo =======================================
echo.

REM Auto open browser
start http://localhost:8080

REM Start Python service
python web_app.py

pause
