#!/bin/bash
# 店小秘 SKU 生成器 - macOS 启动脚本
# 双击此文件或在终端运行即可启动 Web 服务

cd "$(dirname "$0")"

echo "======================================="
echo "  店小秘 SKU 生成器"
echo "======================================="
echo
echo "正在启动服务..."
echo

# 检查 Python 是否安装
if ! command -v python3 &> /dev/null; then
    echo "❌ 错误：未找到 Python3"
    echo "请先安装 Python 3.9 或更高版本"
    echo "下载地址: https://www.python.org/downloads/"
    echo
    read -p "按任意键退出..."
    exit 1
fi

# 检查依赖
echo "检查依赖..."
python3 -c "import flask, pandas, openpyxl" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "首次运行，正在安装依赖..."
    pip3 install flask pandas openpyxl
    echo
fi

# 启动服务
echo "✅ 启动成功！"
echo
echo "访问地址: http://localhost:8080"
echo
echo "提示："
echo "  - 服务已启动，浏览器会自动打开"
echo "  - 如未自动打开，请手动访问上述地址"
echo "  - 按 Ctrl+C 停止服务"
echo
echo "======================================="
echo

# 尝试自动打开浏览器
sleep 1
open http://localhost:8080 2>/dev/null

# 启动 Python 服务
python3 web_app.py
