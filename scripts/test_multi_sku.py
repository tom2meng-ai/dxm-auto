#!/usr/bin/env python3
"""
测试多SKU订单配对功能

使用方法:
    python scripts/test_multi_sku.py --order 5261219-59459
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

# 添加项目根目录到 Python 路径
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.auto_pair_sku import (
    DianXiaoMiAutomation,
    load_config,
    parse_platform_sku,
    generate_single_sku,
    generate_combo_sku,
)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(PROJECT_ROOT / 'logs' / 'test_multi_sku.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)


def test_multi_sku_order(order_no: str, date_str: str = None):
    """测试特定订单的多SKU配对功能

    Args:
        order_no: 订单号（如 5261219-59459）
        date_str: 日期字符串，格式 MMDD
    """
    if not date_str:
        date_str = datetime.now().strftime("%m%d")

    logger.info("=" * 50)
    logger.info(f"测试多SKU功能 - 订单号: {order_no}")
    logger.info(f"日期: {date_str}")
    logger.info("=" * 50)

    config = load_config()
    automation = DianXiaoMiAutomation(
        headless=False,
        slow_mo=config["browser"].get("slow_mo", 100)
    )

    try:
        # 启动浏览器
        automation.start_browser()

        # 导航到订单页面
        automation.navigate_to_orders()

        # 检查登录状态
        if not automation.check_login_status():
            logger.warning("未登录，需要手动登录")
            automation.wait_for_login()
            automation.save_auth_state()

        # 筛选未配对订单
        automation.filter_unpaired_orders()
        automation.page.wait_for_timeout(2000)

        # 搜索特定订单号
        logger.info(f"搜索订单: {order_no}")

        # 在订单搜索框中输入订单号
        search_input = automation.page.locator("input[placeholder*='订单'], input[placeholder*='搜索']").first
        if search_input.count() > 0:
            search_input.fill(order_no)
            automation.page.wait_for_timeout(500)

            # 点击搜索按钮
            search_btn = automation.page.get_by_role("button", name="搜索")
            if search_btn.count() > 0:
                search_btn.first.click()
            else:
                search_input.press("Enter")

            automation.page.wait_for_timeout(2000)

        # 尝试通过订单号定位行
        logger.info("定位订单行...")
        row = automation.page.locator("tr", has=automation.page.locator(f"text={order_no}")).first

        if row.count() == 0:
            logger.error(f"未找到订单: {order_no}")
            automation.save_debug_info("order_not_found")
            return False

        # 获取 row_id
        row_id = row.get_attribute("rowid") if row.count() > 0 else None
        logger.info(f"找到订单，row_id: {row_id}")

        # 打开订单详情
        if not automation.open_order_detail(order_no, None, row_id):
            logger.error("无法打开订单详情")
            return False

        automation.page.wait_for_timeout(1500)
        automation.save_debug_info("multi_sku_test_detail")

        # 检查详情弹窗是否就绪
        if not automation._detail_context_ready():
            logger.error("详情弹窗未就绪")
            return False

        # 提取所有产品信息
        logger.info("\n提取产品信息...")
        products = automation._extract_all_products_from_detail()

        if not products:
            logger.error("未找到产品信息")
            return False

        logger.info(f"\n找到 {len(products)} 个产品:")
        for p in products:
            logger.info(f"  - SKU: {p['sku']}")
            logger.info(f"    Name1: {p['name1']}")
            logger.info(f"    Name2: {p['name2']}")
            logger.info(f"    Quantity: {p.get('quantity', 1)}")

        # 筛选 engraved 产品
        engraved_products = [p for p in products if "engraved" in p["sku"].lower()]
        logger.info(f"\n其中 engraved 产品: {len(engraved_products)} 个")

        # 检测多SKU情况
        sku_groups = {}
        for p in engraved_products:
            sku = p["sku"]
            if sku not in sku_groups:
                sku_groups[sku] = []
            sku_groups[sku].append(p)

        multi_sku_groups = {sku: prods for sku, prods in sku_groups.items() if len(prods) > 1}

        if multi_sku_groups:
            logger.info(f"\n检测到多SKU订单!")
            logger.info(f"共 {len(multi_sku_groups)} 组需要多SKU处理:")
            for sku, prods in multi_sku_groups.items():
                logger.info(f"  - {sku}: {len(prods)} 个产品")
                for p in prods:
                    sku_info = parse_platform_sku(sku)
                    if sku_info and p["name1"]:
                        combo = generate_combo_sku(
                            generate_single_sku(sku_info["product_code"], date_str, p["name1"], p.get("name2", "")),
                            sku_info["card_code"],
                            sku_info["box_type"]
                        )
                        logger.info(f"    -> {combo}")

            # 自动执行配对（非交互模式）
            logger.info("开始多SKU配对...")
            result = automation._process_multi_sku_order(multi_sku_groups, date_str)
            if result:
                logger.info("多SKU配对完成!")
            else:
                logger.error("多SKU配对失败")
            return result
        else:
            logger.info("\n这不是多SKU订单（每个平台SKU只有1个产品）")
            if engraved_products:
                logger.info("将使用单SKU配对逻辑")
            return True

    except Exception as e:
        logger.error(f"测试失败: {e}")
        import traceback
        traceback.print_exc()
        automation.save_debug_info("test_error")
        return False
    finally:
        # 等待几秒让用户查看
        logger.info("等待5秒后关闭浏览器...")
        automation.page.wait_for_timeout(5000)
        automation.close()


def main():
    parser = argparse.ArgumentParser(description="测试多SKU订单配对功能")
    parser.add_argument(
        "--order",
        required=True,
        help="订单号"
    )
    parser.add_argument(
        "--date",
        default=datetime.now().strftime("%m%d"),
        help="日期字符串，格式 MMDD，默认为今天"
    )

    args = parser.parse_args()

    # 确保日志目录存在
    (PROJECT_ROOT / "logs").mkdir(exist_ok=True)

    test_multi_sku_order(args.order, args.date)


if __name__ == "__main__":
    main()
