#!/bin/bash
# 店小秘 SKU 自动配对脚本 - macOS/Linux
# 使用方法: ./自动配对.sh

cd "$(dirname "$0")"

echo "=================================================="
echo "  店小秘 SKU 自动配对脚本"
echo "=================================================="
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
    echo "首次运行，安装依赖..."
    pip3 install playwright pandas openpyxl
    python3 -m playwright install chromium
    echo ""
fi

echo ""
echo "启动自动配对..."
echo ""
python3 scripts/auto_pair_sku.py --max-orders 100
