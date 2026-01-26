#!/bin/bash
# 店小秘登录状态保存脚本 - macOS/Linux
# 首次使用时运行此脚本保存登录状态

cd "$(dirname "$0")"

echo "=================================================="
echo "  店小秘 - 保存登录状态"
echo "=================================================="
echo ""
echo "此脚本会打开浏览器窗口"
echo "请在浏览器中手动登录店小秘"
echo "登录成功后脚本会自动保存登录状态"
echo ""

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "❌ 错误: 未找到 python3"
    echo "请先安装 Python 3.9 或更高版本"
    exit 1
fi

# 检查依赖
echo "检查依赖..."
python3 -c "import playwright" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "安装依赖..."
    pip3 install playwright pandas openpyxl
    python3 -m playwright install chromium
    echo ""
fi

echo ""
echo "启动浏览器进行登录..."
echo ""
python3 scripts/auto_pair_sku.py --save-auth
