@echo off
chcp 65001 >nul
REM 店小秘 SKU 生成器 - Windows 启动脚本
REM 双击此文件即可启动 Web 服务

cd /d "%~dp0"

echo =======================================
echo   店小秘 SKU 生成器
echo =======================================
echo.
echo 正在启动服务...
echo.

REM 检查 Python 是否安装
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ 错误：未找到 Python
    echo 请先安装 Python 3.9 或更高版本
    echo 下载地址: https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

REM 检查依赖
echo 检查依赖...
python -c "import flask, pandas, openpyxl" >nul 2>&1
if errorlevel 1 (
    echo 首次运行，正在安装依赖...
    pip install flask pandas openpyxl
    echo.
)

REM 启动服务
echo ✅ 启动成功！
echo.
echo 访问地址: http://localhost:8080
echo.
echo 提示：
echo   - 服务已启动，浏览器会自动打开
echo   - 如未自动打开，请手动访问上述地址
echo   - 关闭此窗口会停止服务
echo.
echo =======================================
echo.

REM 尝试自动打开浏览器
start http://localhost:8080

REM 启动 Python 服务
python web_app.py

pause
