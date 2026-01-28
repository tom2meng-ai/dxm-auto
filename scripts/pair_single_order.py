#!/usr/bin/env python3
"""
单订单配对脚本
直接配对指定订单，可观察整个流程
使用和自动配对相同的浏览器启动方式
"""

import sys
import os
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.sync_api import sync_playwright
from scripts.auto_pair_sku import DianXiaoMiAutomation
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 登录状态保存路径（和自动配对相同）
AUTH_STATE_PATH = Path(__file__).parent.parent / "data" / "auth_state.json"


def pair_single_order(order_no: str, new_sku: str = None):
    """
    配对单个订单

    Args:
        order_no: 订单号（如 XMHDUNR12345 或平台订单号）
        new_sku: 要配对的新SKU（可选）
    """
    logger.info(f"开始配对订单: {order_no}")

    playwright = sync_playwright().start()

    # 启动浏览器（和自动配对相同方式）
    browser = playwright.chromium.launch(
        headless=False,
        slow_mo=300  # 放慢操作，便于观察
    )

    # 加载已保存的登录状态
    if AUTH_STATE_PATH.exists():
        logger.info("加载已保存的登录状态...")
        context = browser.new_context(storage_state=str(AUTH_STATE_PATH))
    else:
        logger.info("未找到登录状态，使用新会话（需要登录）")
        context = browser.new_context()

    page = context.new_page()
    page.set_viewport_size({"width": 1280, "height": 800})

    # 创建自动化实例
    automation = DianXiaoMiAutomation(page)

    try:
        # 导航到待审核订单页面
        logger.info("导航到待审核订单页面...")
        page.goto("https://www.dianxiaomi.com/web/order/paid?go=m100")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2000)

        # 检查是否需要登录
        if "login" in page.url.lower() or page.locator("input[type='password']").count() > 0:
            logger.info("请在浏览器中登录店小秘...")
            logger.info("登录后会自动继续...")
            page.wait_for_url("**/web/order/**", timeout=300000)  # 等待5分钟
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(2000)
            # 保存登录状态
            AUTH_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            context.storage_state(path=str(AUTH_STATE_PATH))
            logger.info(f"登录状态已保存到: {AUTH_STATE_PATH}")

        # 搜索订单
        logger.info(f"搜索订单: {order_no}")

        # 选择搜索类型为"店小秘订单号"
        try:
            search_type_select = page.locator(".order-search select").first
            if search_type_select.count() > 0:
                search_type_select.select_option(label="店小秘订单号")
                page.wait_for_timeout(500)
        except Exception as e:
            logger.warning(f"选择搜索类型: {e}")

        # 输入订单号
        search_input = page.get_by_placeholder("请输入搜索内容")
        if search_input.count() > 0:
            search_input.fill(order_no)
            page.wait_for_timeout(300)

        # 点击搜索按钮
        search_btn = page.get_by_role("button", name="搜 索")
        if search_btn.count() > 0:
            search_btn.click()
            page.wait_for_timeout(2000)
            logger.info("搜索完成")

        # 点击详情按钮
        logger.info("点击详情按钮...")
        if not automation.open_order_detail(order_no=order_no):
            logger.error("点击详情按钮失败")
            input("\n按 Enter 键关闭浏览器...")
            browser.close()
            playwright.stop()
            return False

        page.wait_for_timeout(1000)
        logger.info("详情弹窗已打开")

        # 点击配对商品SKU链接
        logger.info("点击配对商品SKU链接...")
        if automation.click_pair_sku_button():
            logger.info("成功点击配对商品SKU链接，配对弹窗已打开")

            # 如果指定了新SKU，继续配对流程
            if new_sku:
                logger.info(f"搜索并配对SKU: {new_sku}")
                if automation.search_and_select_sku(new_sku):
                    logger.info(f"成功配对SKU: {new_sku}")
                else:
                    logger.error(f"配对SKU失败: {new_sku}")
        else:
            logger.error("点击配对商品SKU链接失败")

        # 暂停，让用户查看结果
        input("\n配对流程完成，按 Enter 键关闭浏览器...")

    except Exception as e:
        logger.error(f"配对过程出错: {e}")
        input("\n出错了，按 Enter 键关闭浏览器...")

    finally:
        browser.close()
        playwright.stop()

    return True


if __name__ == "__main__":
    print("=" * 50)
    print("  店小秘 单订单配对工具")
    print("=" * 50)
    print()

    # 获取订单号
    if len(sys.argv) > 1:
        order_no = sys.argv[1]
    else:
        order_no = input("请输入要配对的订单号: ").strip()

    if not order_no:
        print("错误: 订单号不能为空")
        sys.exit(1)

    # 可选：指定要配对的SKU
    new_sku = None
    if len(sys.argv) > 2:
        new_sku = sys.argv[2]

    print(f"\n订单号: {order_no}")
    if new_sku:
        print(f"配对SKU: {new_sku}")
    print()

    pair_single_order(order_no, new_sku)
