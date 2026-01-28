#!/bin/bash
#
# 单订单配对脚本
# 用法: ./test_pair.sh [订单号] [SKU]
#

# 切换到项目目录
cd "$(dirname "$0")" || exit 1

# 颜色定义
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}   店小秘 单订单配对工具${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# 获取订单号
if [ -n "$1" ]; then
    ORDER_NO="$1"
else
    echo -n "请输入要配对的订单号: "
    read ORDER_NO
fi

if [ -z "$ORDER_NO" ]; then
    echo "错误: 订单号不能为空"
    exit 1
fi

echo ""
echo -e "${YELLOW}订单号: ${ORDER_NO}${NC}"

# 可选的SKU参数
if [ -n "$2" ]; then
    echo -e "${YELLOW}配对SKU: ${2}${NC}"
fi

echo ""
echo "启动浏览器，请观察配对流程..."
echo ""

# 运行配对脚本
python3 scripts/pair_single_order.py "$ORDER_NO" "$2"

exit $?
