#!/usr/bin/env python3
"""
店小秘 SKU 自动配对脚本

功能：
1. 使用浏览器保存的登录状态访问店小秘
2. 自动筛选未配对 SKU 的订单
3. 根据生成规则自动配对 SKU

使用方法：
    # 首次运行，需要手动登录保存状态
    python scripts/auto_pair_sku.py --save-auth

    # 正常运行
    python scripts/auto_pair_sku.py
"""

import argparse
import json
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright, Page, Browser, TimeoutError as PlaywrightTimeout

# 从共享模块导入
from sku_utils import (
    PROJECT_ROOT,
    STORE_NAME,
    load_card_mapping,
    extract_card_code_smart,
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
        logging.FileHandler(PROJECT_ROOT / 'logs' / 'auto_pair.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# 常量
AUTH_STATE_PATH = PROJECT_ROOT / "config" / "auth_state.json"
PROGRESS_FILE = PROJECT_ROOT / "data" / "pair_progress.json"


def load_config() -> dict:
    """加载配置文件"""
    config_path = PROJECT_ROOT / "config" / "config.json"
    default_config = {
        "store_name": "Michael",
        "red_box_sku": "Michael-RED BOX",
        "dianxiaomi": {
            "base_url": "https://www.dianxiaomi.com",
            "order_page": "/web/order/paid?go=m100"
        },
        "browser": {
            "headless": False,
            "slow_mo": 100
        }
    }
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return {**default_config, **json.load(f)}
    except FileNotFoundError:
        return default_config


def load_progress() -> dict:
    """加载已处理的订单进度"""
    try:
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"processed_orders": [], "last_run": None}


def save_progress(progress: dict):
    """保存处理进度"""
    progress["last_run"] = datetime.now().isoformat()
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


class DianXiaoMiAutomation:
    """店小秘自动化操作类"""

    def __init__(self, headless: bool = False, slow_mo: int = 100):
        self.headless = headless
        self.slow_mo = slow_mo
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.config = load_config()
        self.card_mapping = load_card_mapping()
        self.progress = load_progress()

    def start_browser(self):
        """启动浏览器"""
        playwright = sync_playwright().start()
        self.browser = playwright.chromium.launch(
            headless=self.headless,
            slow_mo=self.slow_mo
        )

        # 尝试加载已保存的登录状态
        if AUTH_STATE_PATH.exists():
            logger.info("加载已保存的登录状态...")
            context = self.browser.new_context(storage_state=str(AUTH_STATE_PATH))
        else:
            logger.info("未找到登录状态，使用新会话")
            context = self.browser.new_context()

        self.page = context.new_page()
        self.page.set_viewport_size({"width": 1280, "height": 800})

    def close(self):
        """关闭浏览器"""
        if self.browser:
            self.browser.close()

    def save_auth_state(self):
        """保存登录状态"""
        if self.page:
            AUTH_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            self.page.context.storage_state(path=str(AUTH_STATE_PATH))
            logger.info(f"登录状态已保存到: {AUTH_STATE_PATH}")

    def navigate_to_orders(self):
        """导航到订单页面"""
        base_url = self.config["dianxiaomi"]["base_url"]
        order_page = self.config["dianxiaomi"]["order_page"]
        url = f"{base_url}{order_page}"

        logger.info(f"访问订单页面: {url}")
        self.page.goto(url)
        self.page.wait_for_load_state("networkidle")

    def check_login_status(self) -> bool:
        """检查登录状态"""
        try:
            # 等待页面加载
            self.page.wait_for_timeout(3000)

            # 检查是否跳转到登录页
            current_url = self.page.url.lower()
            if "login" in current_url or "passport" in current_url:
                return False

            # 检查是否有订单相关元素或主界面元素
            self.page.wait_for_selector(".order-list, .el-table, .layout-main, .main-content, #app", timeout=10000)
            return True
        except PlaywrightTimeout:
            # 即使超时，如果不在登录页也认为已登录
            current_url = self.page.url.lower()
            if "login" not in current_url and "passport" not in current_url and "dianxiaomi.com" in current_url:
                return True
            return False

    def wait_for_login(self, max_wait_seconds: int = 300):
        """等待用户手动登录"""
        logger.info("请在浏览器中手动登录店小秘...")
        logger.info("登录成功后，脚本将自动继续")

        # 等待登录成功（检测到订单页面元素）
        waited = 0
        while waited < max_wait_seconds:
            try:
                self.page.wait_for_selector(".order-list, .el-table, .layout-main", timeout=5000)
                if "login" not in self.page.url.lower():
                    logger.info("检测到登录成功!")
                    break
            except PlaywrightTimeout:
                time.sleep(2)
                waited += 2
        else:
            self.save_debug_info("login_timeout")
            raise PlaywrightTimeout(f"等待登录超时，已等待 {max_wait_seconds} 秒")

    def save_debug_info(self, name: str):
        """保存调试信息（截图和HTML）"""
        try:
            debug_dir = PROJECT_ROOT / "logs" / "debug"
            debug_dir.mkdir(parents=True, exist_ok=True)

            # 保存截图
            self.page.screenshot(path=str(debug_dir / f"{name}.png"))
            logger.info(f"截图已保存: {debug_dir / f'{name}.png'}")

            # 保存 HTML
            html_content = self.page.content()
            with open(debug_dir / f"{name}.html", "w", encoding="utf-8") as f:
                f.write(html_content)
            logger.info(f"HTML已保存: {debug_dir / f'{name}.html'}")
        except Exception as e:
            logger.error(f"保存调试信息失败: {e}")

    def filter_unpaired_orders(self):
        """筛选未配对 SKU 的订单"""
        logger.info("筛选未配对 SKU 订单...")

        try:
            # 等待页面加载完成
            self.page.wait_for_timeout(3000)
            self._dismiss_overlays()

            # 尝试多轮点击"未配对SKU"
            clicked = False
            for attempt in range(3):
                logger.info(f"筛选尝试 {attempt + 1}/3")

                # 方法1: 使用正则匹配（含数量）
                unpaired_link = self.page.locator("text=/未配对SKU\\(\\d+\\)/")
                if unpaired_link.count() > 0:
                    unpaired_link.first.click()
                    clicked = True
                    logger.info("方法1成功: text=/未配对SKU\\(\\d+\\)/")

                # 方法1.1: 纯文本匹配
                if not clicked:
                    unpaired_link = self.page.locator("text=未配对SKU")
                    if unpaired_link.count() > 0:
                        unpaired_link.first.click()
                        clicked = True
                        logger.info("方法1.1成功: text=未配对SKU")

                # 方法2: 精确文本匹配（带数字）
                if not clicked:
                    links = self.page.locator("a, span, button, div")
                    count = links.count()
                    for i in range(count):
                        try:
                            text = links.nth(i).inner_text()
                            if "未配对SKU" in text or "未配对" in text:
                                links.nth(i).click()
                                clicked = True
                                logger.info(f"方法2成功: 找到文本 '{text}'")
                                break
                        except Exception:
                            continue

                # 方法3: 通过包含关键字的元素
                if not clicked:
                    selectors = [
                        "a:has-text('未配对SKU')",
                        "a:has-text('未配对')",
                        "button:has-text('未配对')",
                        "span:has-text('未配对')",
                        "[class*='filter']:has-text('未配对')",
                        "div:has-text('未配对SKU')"
                    ]
                    for sel in selectors:
                        try:
                            el = self.page.locator(sel).first
                            if el.count() > 0:
                                el.click()
                                clicked = True
                                logger.info(f"方法3成功: {sel}")
                                break
                        except Exception:
                            continue

                # 方法4: 先打开筛选面板再点击
                if not clicked:
                    filter_btns = [
                        "button:has-text('筛选')",
                        "a:has-text('筛选')",
                        "button:has-text('过滤')",
                        "a:has-text('过滤')",
                        "[class*='filter']"
                    ]
                    for sel in filter_btns:
                        try:
                            btn = self.page.locator(sel).first
                            if btn.count() > 0:
                                btn.click()
                                self.page.wait_for_timeout(500)
                                break
                        except Exception:
                            continue

                if clicked:
                    self.page.wait_for_timeout(2000)
                    try:
                        self.page.wait_for_load_state("networkidle", timeout=8000)
                    except PlaywrightTimeout:
                        pass
                    logger.info("筛选完成")
                    break

                self.page.wait_for_timeout(1000)

            if not clicked:
                logger.warning("未找到'未配对SKU'筛选选项")
                self.save_debug_info("filter_not_found")

        except PlaywrightTimeout:
            logger.warning("筛选超时，可能没有未配对订单")
            self.save_debug_info("filter_timeout")

    def get_order_list(self, only_engraved: bool = True) -> list:
        """获取订单列表

        Args:
            only_engraved: 是否只返回 engraved 订单，默认 True
        """
        orders = []
        engraved_orders = []
        try:
            # 等待订单列表加载
            self.page.wait_for_timeout(2000)

            # 店小秘的订单列表结构：每个订单是一个区块
            # 尝试多种可能的选择器
            selectors = [
                "tr[data-id]",  # 表格行带 data-id
                ".order-item",
                ".order-row",
                "table tbody tr",
                "[class*='order']"
            ]

            order_rows = []
            for selector in selectors:
                rows = self.page.query_selector_all(selector)
                if rows and len(rows) > 0:
                    order_rows = rows
                    logger.info(f"使用选择器 '{selector}' 找到 {len(rows)} 行")
                    break

            if not order_rows:
                # 尝试通过订单号格式查找
                logger.info("尝试通过订单号查找订单...")
                # 店小秘订单号通常以字母开头，如 XMHDUNR08723
                order_links = self.page.query_selector_all("a[href*='order'], td a")
                for link in order_links:
                    text = link.inner_text().strip()
                    # 检查是否像订单号（字母+数字组合）
                    if text and len(text) > 5 and any(c.isalpha() for c in text) and any(c.isdigit() for c in text):
                        orders.append({
                            "order_no": text,
                            "platform_sku": "",  # 需要进入详情获取
                            "element": link
                        })

            # 备用：从包含"详情"的行中提取
            if not order_rows:
                detail_rows = self.page.query_selector_all("table tr")
                for row in detail_rows:
                    try:
                        if row.query_selector("a:has-text('详情')"):
                            order_rows.append(row)
                    except Exception:
                        continue

            # 从行中提取信息
            for row in order_rows:
                order_info = self._extract_order_info(row)
                if order_info:
                    orders.append(order_info)

            logger.info(f"找到 {len(orders)} 个订单")

            # 筛选 engraved 订单
            if only_engraved:
                for order in orders:
                    platform_sku = order.get("platform_sku", "")
                    if platform_sku and "engraved" in platform_sku.lower():
                        engraved_orders.append(order)

                logger.info(f"其中 engraved 订单: {len(engraved_orders)} 个")
                return engraved_orders

        except PlaywrightTimeout:
            logger.warning("获取订单列表超时")
        except Exception as e:
            logger.error(f"获取订单列表出错: {e}")

        return orders if not only_engraved else engraved_orders

    def _extract_order_info(self, row) -> Optional[dict]:
        """从订单行提取信息"""
        try:
            class_attr = row.get_attribute("class") or ""
            if "first-level-row" in class_attr:
                return None

            # 尝试多种方式获取订单号
            order_no = ""
            platform_sku = ""

            # 获取订单号 - 订单号在 .orderCode 的首个指示元素
            order_code_el = row.query_selector(".orderCode .pointer")
            if order_code_el:
                candidate = order_code_el.inner_text().strip()
                if candidate and not candidate.startswith("#"):
                    order_no = candidate

            if not order_no:
                bag_el = row.query_selector(".orderBagInfo a")
                if bag_el:
                    candidate = bag_el.inner_text().strip()
                    if candidate:
                        order_no = candidate

            # 获取所有文本，尝试找 SKU
            sku_elements = row.query_selector_all(".order-sku__name")
            for el in sku_elements:
                text = el.inner_text().strip()
                if text and parse_platform_sku(text):
                    platform_sku = text
                    break

            if not platform_sku:
                all_text = row.inner_text()
                # SKU 通常包含 "-" 和特定格式
                sku_match = re.search(r'[A-Z]\d+[-][A-Z][-]', all_text)
                if sku_match:
                    # 找到类似 J20-G- 的模式，提取完整 SKU
                    start = sku_match.start()
                    end = all_text.find('\n', start)
                    if end == -1:
                        end = min(start + 50, len(all_text))
                    platform_sku = all_text[start:end].strip()

            # 提取 Name1, Name2（从行文本中）
            name1 = ""
            name2 = ""
            all_text = row.inner_text()
            name1_match = re.search(r'Name\s*1\s*[:：]\s*([^\n\r]+)', all_text, re.IGNORECASE)
            if name1_match:
                name1 = name1_match.group(1).strip()
            name2_match = re.search(r'Name\s*2\s*[:：]\s*([^\n\r]+)', all_text, re.IGNORECASE)
            if name2_match:
                name2 = name2_match.group(1).strip()

            if order_no:
                return {
                    "order_no": order_no,
                    "platform_sku": platform_sku,
                    "row_element": row,
                    "row_id": row.get_attribute("rowid"),
                    "name1": name1,
                    "name2": name2
                }
        except Exception as e:
            logger.debug(f"提取订单信息失败: {e}")

        return None

    def open_order_detail(self, order_no: str, row_element=None, row_id: str = None):
        """打开订单详情"""
        logger.info(f"打开订单详情: {order_no}")

        try:
            self._dismiss_overlays()

            def _detail_visible() -> bool:
                """检查详情弹窗是否可见"""
                if self._get_detail_container():
                    return True
                for frame in self.page.frames:
                    try:
                        frame_title = frame.locator("text=包裹").first
                        if frame_title.count() > 0 and frame_title.is_visible():
                            return True
                        frame_link = frame.locator("text=配对商品SKU").first
                        if frame_link.count() > 0 and frame_link.is_visible():
                            return True
                    except Exception:
                        continue
                return False

            def _wait_detail_visible(timeout_ms: int = 8000) -> bool:
                """等待详情弹窗出现"""
                start = time.time()
                while (time.time() - start) * 1000 < timeout_ms:
                    if _detail_visible():
                        return True
                    self.page.wait_for_timeout(300)
                return False

            # 核心方法：使用 getByRole 精确定位"详情"链接
            # 根据 playwright codegen 录制结果：page.getByRole('link', { name: '详情' })

            # 先找到包含当前订单号的行
            if row_id:
                logger.info(f"尝试使用 row_id 定位: {row_id}")
                row_locator = self.page.locator(f"tr[rowid='{row_id}']").first
                if row_locator.count() > 0:
                    row_locator.scroll_into_view_if_needed()
                    self.page.wait_for_timeout(300)

                    # 在该行内查找"详情"链接
                    detail_link = row_locator.get_by_role("link", name="详情")
                    if detail_link.count() > 0:
                        logger.info("找到详情链接，点击...")
                        detail_link.first.click(timeout=5000)
                        self.page.wait_for_timeout(1500)
                        if _wait_detail_visible():
                            logger.info("详情弹窗已打开")
                            return True

            # 备用方案：通过订单号定位行
            row_by_order = self.page.locator("tr", has=self.page.locator(f"text={order_no}")).first
            if row_by_order.count() > 0:
                row_by_order.scroll_into_view_if_needed()
                self.page.wait_for_timeout(300)

                detail_link = row_by_order.get_by_role("link", name="详情")
                if detail_link.count() > 0:
                    logger.info("通过订单号找到详情链接，点击...")
                    detail_link.first.click(timeout=5000)
                    self.page.wait_for_timeout(1500)
                    if _wait_detail_visible():
                        logger.info("详情弹窗已打开")
                        return True

            # 最后备用：全局查找第一个"详情"链接（不推荐，可能点错）
            logger.warning("无法在行内定位，尝试全局查找详情链接")
            all_detail_links = self.page.get_by_role("link", name="详情")
            if all_detail_links.count() > 0:
                logger.info(f"找到 {all_detail_links.count()} 个详情链接")
                all_detail_links.first.click(timeout=5000)
                self.page.wait_for_timeout(1500)
                if _wait_detail_visible():
                    logger.info("详情弹窗已打开")
                    return True

            self.save_debug_info("detail_button_not_found")
            logger.error("未找到详情链接")

        except PlaywrightTimeout:
            logger.error(f"打开订单详情超时: {order_no}")
            self.save_debug_info("detail_open_timeout")
        except Exception as e:
            logger.error(f"打开订单详情失败: {e}")
            self.save_debug_info("detail_open_error")

        return False


    def click_pair_sku_button(self):
        """点击配对商品SKU链接"""
        logger.info("点击配对商品SKU链接...")

        try:
            # 注意：不要调用 _dismiss_overlays()，因为详情弹窗需要保持打开
            self.page.wait_for_timeout(500)

            # 核心方法：使用 getByRole 精确定位"配对商品SKU"链接
            # 根据 playwright codegen 录制结果：page.getByRole('link', { name: '配对商品SKU' })
            pair_link = self.page.get_by_role("link", name="配对商品SKU")
            if pair_link.count() > 0:
                logger.info(f"找到 {pair_link.count()} 个'配对商品SKU'链接")
                pair_link.first.click(timeout=5000)
                self.page.wait_for_timeout(1500)
                logger.info("配对商品SKU链接点击成功")
                return True

            # 备用方案：使用文本匹配
            pair_link_text = self.page.locator("a:has-text('配对商品SKU')").first
            if pair_link_text.count() > 0:
                logger.info("通过文本匹配找到配对商品SKU链接")
                pair_link_text.click(timeout=5000)
                self.page.wait_for_timeout(1500)
                logger.info("配对商品SKU链接点击成功（备用方案）")
                return True

            self.save_debug_info("pair_button_not_found")
            logger.warning("未找到配对商品SKU链接，可能订单已配对")

        except Exception as e:
            logger.error(f"点击配对商品SKU链接失败: {e}")
            self.save_debug_info("pair_button_error")

        return False

    def is_order_paired(self) -> bool:
        """检查当前订单是否已配对"""
        try:
            self.page.wait_for_timeout(500)
            detail_container = self._get_detail_container()
            if not detail_container:
                for frame in self.page.frames:
                    try:
                        frame_pair = frame.locator("text=配对商品SKU").first
                        if frame_pair.count() > 0 and frame_pair.is_visible():
                            logger.info("检测到未配对订单（frame存在配对商品SKU）")
                            return False
                        frame_change = frame.locator("text=更换").first
                        frame_unbind = frame.locator("text=解除").first
                        if (frame_change.count() > 0 and frame_change.is_visible()) or (
                            frame_unbind.count() > 0 and frame_unbind.is_visible()
                        ):
                            logger.info("检测到已配对订单（frame存在更换/解除）")
                            return True
                    except Exception:
                        continue
                logger.warning("未检测到订单详情弹窗")
                return False

            pair_link = detail_container.locator("text=配对商品SKU").first
            if pair_link.count() > 0:
                logger.info("检测到未配对订单（详情弹窗存在配对商品SKU）")
                return False

            if detail_container.locator("text=更换").count() > 0 or detail_container.locator("text=解除").count() > 0:
                logger.info("检测到已配对订单（存在更换/解除）")
                return True

            logger.warning("未找到配对入口，无法确认已配对，跳过审核")
            return False
        except Exception as e:
            logger.debug(f"检查配对状态失败: {e}")
            return False  # 出错时默认未配对，确保尝试配对

    def click_review_button(self) -> bool:
        """点击审核按钮"""
        logger.info("点击审核按钮...")
        try:
            self._dismiss_overlays()
            # 审核按钮通常是橙色/红色的
            btn = self.page.locator("button:has-text('审核')").first
            if btn.count() > 0 and btn.is_visible():
                btn.click(timeout=5000)
                self.page.wait_for_timeout(1000)
                logger.info("审核按钮点击成功")
                return True
            logger.warning("未找到审核按钮")
        except Exception as e:
            logger.error(f"点击审核按钮失败: {e}")
        return False

    def click_next_order(self) -> bool:
        """点击下一个按钮

        Returns:
            True: 成功切换到下一个订单
            False: 已经是最后一个订单，或无法切换
        """
        logger.info("点击下一个按钮...")
        try:
            # 注意：不要调用 _dismiss_overlays()，因为详情弹窗需要保持打开

            # 使用 getByRole 精确定位"下一个"按钮
            next_btn = self.page.get_by_role("button", name="下一个")
            clicked = False

            if next_btn.count() > 0:
                next_btn.first.click(timeout=5000, force=True)
                clicked = True
            else:
                # 备用方案：尝试多种选择器
                next_selectors = [
                    "button:has-text('下一个')",
                    "a:has-text('下一个')",
                    "span:has-text('下一个')",
                    "text=下一个"
                ]

                for selector in next_selectors:
                    btn = self.page.locator(selector).first
                    if btn.count() > 0:
                        try:
                            btn.click(timeout=3000, force=True)
                            clicked = True
                            break
                        except Exception:
                            continue

            if not clicked:
                # 备用方案：使用 JavaScript 点击
                clicked = self.page.evaluate("""
                    () => {
                        const btns = document.querySelectorAll('button, a, span');
                        for (const btn of btns) {
                            if (btn.innerText && btn.innerText.includes('下一个')) {
                                btn.click();
                                return true;
                            }
                        }
                        return false;
                    }
                """)

            if not clicked:
                logger.warning("未找到下一个按钮")
                return False

            # 等待页面响应
            self.page.wait_for_timeout(1500)

            # 检测是否出现"最后一个订单"的提示（依赖店小秘的实际提示）
            if self._is_last_order():
                logger.info("🏁 已经是最后一个订单，停止处理")
                return False

            logger.info("切换到下一个订单")
            return True

        except Exception as e:
            logger.error(f"点击下一个按钮失败: {e}")
        return False

    def _is_last_order(self) -> bool:
        """检测是否已经是最后一个订单

        Returns:
            True: 是最后一个订单
            False: 不是最后一个订单
        """
        try:
            # 检测常见的"最后一个订单"提示文本
            last_order_indicators = [
                "最后一个",
                "已经是最后",
                "没有更多",
                "无更多订单",
                "已是最后",
                "last order",
                "no more"
            ]

            # 检测页面上是否出现提示信息（通常是 message 或 notification）
            message_selectors = [
                ".ant-message",
                ".ant-notification",
                ".el-message",
                ".message",
                ".toast",
                ".ant-modal-body"
            ]

            for selector in message_selectors:
                elements = self.page.locator(selector).all()
                for el in elements:
                    try:
                        if el.is_visible():
                            text = el.inner_text().lower()
                            for indicator in last_order_indicators:
                                if indicator.lower() in text:
                                    logger.debug(f"检测到最后一个订单提示: {text}")
                                    return True
                    except Exception:
                        continue

            # 备用：检查整个详情弹窗的文本
            detail_container = self._get_detail_container()
            if detail_container:
                try:
                    container_text = detail_container.inner_text().lower()
                    for indicator in last_order_indicators:
                        if indicator.lower() in container_text:
                            logger.debug(f"在详情弹窗中检测到最后一个订单提示")
                            return True
                except Exception:
                    pass

            # 检查"下一个"按钮是否被禁用
            next_btn = self.page.get_by_role("button", name="下一个").first
            if next_btn.count() > 0:
                try:
                    is_disabled = next_btn.is_disabled()
                    if is_disabled:
                        logger.debug("下一个按钮已禁用")
                        return True
                except Exception:
                    pass

        except Exception as e:
            logger.debug(f"检测最后一个订单时出错: {e}")

        return False

    def _dismiss_overlays(self):
        """关闭可能遮挡操作的弹窗"""
        try:
            # 优先关闭"同步订单"弹窗
            sync_modal = self.page.locator(".ant-modal-root:has-text('同步订单')").first
            if sync_modal.count() > 0:
                close_btn = sync_modal.locator(".ant-modal-close, button:has-text('关闭')").first
                if close_btn.count() > 0:
                    close_btn.click(timeout=2000, force=True)
                    self.page.wait_for_timeout(500)

            # 关闭"产品动态"弹窗
            modal = self.page.locator(".ant-modal-root:has-text('产品动态')").first
            if modal.count() > 0:
                close_btn = modal.locator(".ant-modal-close, button:has-text('关闭')").first
                if close_btn.count() > 0:
                    close_btn.click(timeout=2000, force=True)
                    self.page.wait_for_timeout(300)

            close_selectors = [
                ".ant-modal-close",
                "button:has-text('关闭')",
                "button:has-text('我知道了')",
                "button:has-text('知道了')"
            ]
            for selector in close_selectors:
                btns = self.page.locator(selector)
                count = btns.count()
                for i in range(count):
                    try:
                        btn = btns.nth(i)
                        if not btn.is_visible():
                            continue
                        btn.click(timeout=2000, force=True)
                        self.page.wait_for_timeout(300)
                    except Exception:
                        continue

            # 兜底：按 ESC 关闭遮罩
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(300)

            # 如果弹窗仍在，直接隐藏遮罩层
            overlay_visible = self.page.locator(".ant-modal-wrap, .ant-modal-mask").first
            if overlay_visible.count() > 0:
                self.page.evaluate(
                    """
                    () => {
                      const selectors = [
                        '.ant-modal-root',
                        '.ant-modal-wrap',
                        '.ant-modal-mask',
                        '#theNewestModalLabelFrame'
                      ];
                      selectors.forEach((sel) => {
                        document.querySelectorAll(sel).forEach((el) => {
                          el.style.display = 'none';
                          el.style.pointerEvents = 'none';
                        });
                      });
                    }
                    """
                )
                self.page.wait_for_timeout(300)
        except Exception:
            pass

    def _get_detail_container(self):
        """获取订单详情弹窗容器"""
        selectors = [
            "dialog:has-text('包裹')",
            "dialog:has-text('详情 - 来源')",
            ".ant-modal:has-text('包裹')",
            ".ant-modal:has-text('详情 - 来源')",
            ".ant-modal-wrap:has-text('包裹')",
            ".ant-modal-wrap:has-text('详情 - 来源')",
            ".ant-modal.order-default-modal"
        ]
        for sel in selectors:
            container = self.page.locator(sel).first
            if container.count() == 0:
                continue
            try:
                if container.is_visible():
                    return container
            except Exception:
                continue
        return None

    def _detail_context_ready(self) -> bool:
        """判断详情弹窗是否可用"""
        # 方法1：检测详情容器
        if self._get_detail_container():
            return True

        # 方法2：检测页面上是否有详情相关元素
        try:
            # 检测常见的详情弹窗元素
            detail_indicators = [
                "text=包裹",
                "text=配对商品SKU",
                "text=更换",
                "text=解除",
                "text=商品信息",
                "text=订单信息",
                ".ant-modal-body",
                ".order-detail",
            ]
            for selector in detail_indicators:
                el = self.page.locator(selector).first
                if el.count() > 0:
                    try:
                        if el.is_visible():
                            return True
                    except Exception:
                        pass
        except Exception:
            pass

        # 方法3：检测 iframe 中的元素
        for frame in self.page.frames:
            try:
                if frame.locator("text=包裹").first.is_visible():
                    return True
                if frame.locator("text=配对商品SKU").first.is_visible():
                    return True
                if frame.locator("text=更换").first.is_visible():
                    return True
                if frame.locator("text=解除").first.is_visible():
                    return True
            except Exception:
                continue

        # 方法4：如果有任何弹窗打开，也认为就绪
        try:
            modal = self.page.locator(".ant-modal, .modal, dialog").first
            if modal.count() > 0 and modal.is_visible():
                return True
        except Exception:
            pass

        return False

    def search_and_select_sku(self, sku: str) -> bool:
        """搜索并选择 SKU"""
        logger.info(f"搜索 SKU: {sku}")

        try:
            # 等待配对弹窗加载 - 增加等待时间
            logger.info("等待配对弹窗加载...")
            self.page.wait_for_timeout(3000)

            # 检查弹窗是否已打开
            modal_selectors = [".ant-modal", ".modal", "dialog"]
            modal_found = False
            for sel in modal_selectors:
                if self.page.locator(sel).count() > 0:
                    modal_found = True
                    logger.info(f"检测到弹窗: {sel}")
                    break
            if not modal_found:
                logger.warning("未检测到配对弹窗，可能打开失败")
                return False

            # 方法1: 查找所有输入框，优先选择包含"搜索"或placeholder相关的
            input_elements = self.page.query_selector_all("input")
            search_input = None

            for inp in input_elements:
                try:
                    placeholder = (inp.get_attribute("placeholder") or "").lower()
                    name = (inp.get_attribute("name") or "").lower()
                    id_attr = (inp.get_attribute("id") or "").lower()

                    if ("search" in placeholder or "搜索" in placeholder or
                        "search" in name or "sku" in name or
                        "search" in id_attr or "sku" in id_attr):
                        if inp.is_visible():
                            search_input = inp
                            logger.info(f"找到搜索输入框 (placeholder: {placeholder}, name: {name}, id: {id_attr})")
                            break
                except Exception:
                    continue

            # 方法2: 如果没找到，查找弹窗内的第一个可见输入框
            if not search_input:
                modals = self.page.query_selector_all(".ant-modal, .modal, dialog")
                for modal in modals:
                    inputs = modal.query_selector_all("input")
                    for inp in inputs:
                        if inp.is_visible():
                            search_input = inp
                            logger.info("在弹窗中找到输入框")
                            break
                    if search_input:
                        break

            # 方法3: 兜底查找所有可见输入框
            if not search_input:
                for inp in input_elements:
                    if inp.is_visible():
                        search_input = inp
                        logger.info("使用第一个可见输入框")
                        break

            if not search_input:
                self.save_debug_info("pair_search_input_not_found")
                logger.warning("未找到搜索输入框")
                return False

            logger.info("输入SKU...")

            # 清空并输入SKU
            search_input.fill("")
            search_input.fill(sku)
            self.page.wait_for_timeout(500)

            # 点击搜索按钮
            search_buttons = self.page.query_selector_all("button, input[type='submit']")
            search_btn = None

            for btn in search_buttons:
                try:
                    text = btn.inner_text().strip().lower()
                    if "搜索" in text or "search" in text or "查询" in text or "find" in text:
                        if btn.is_visible():
                            search_btn = btn
                            logger.info(f"找到搜索按钮: '{text}'")
                            break
                except Exception:
                    continue

            if search_btn:
                search_btn.click(force=True)
                logger.info("点击搜索按钮")
            else:
                # 备用：按回车
                search_input.press("Enter")
                logger.info("按回车搜索")

            # 等待搜索结果加载
            self.page.wait_for_timeout(2000)

            # 点击"选择"按钮
            select_buttons = self.page.query_selector_all("button, a, span")
            select_btn = None

            for btn in select_buttons:
                try:
                    text = btn.inner_text().strip().lower()
                    if "选择" in text or "select" in text or "选择" in text:
                        if btn.is_visible():
                            select_btn = btn
                            logger.info(f"找到选择按钮: '{text}'")
                            break
                except Exception:
                    continue

            if select_btn:
                select_btn.click(force=True)
                self.page.wait_for_timeout(1500)
                logger.info(f"点击选择按钮: {sku}")

                # 点击"选择"后会弹出确认弹窗，需要点击"确定"按钮
                # 弹窗有两个选项：默认是"仅配对这个订单"，直接点确定即可
                confirm_btn = self.page.get_by_role("button", name="确定")
                if confirm_btn.count() > 0:
                    logger.info("找到确定按钮，点击确认...")
                    confirm_btn.first.click(timeout=5000)
                    self.page.wait_for_timeout(1000)
                    logger.info(f"SKU 配对成功: {sku}")
                    return True
                else:
                    # 备用方案：通过文本查找确定按钮
                    confirm_btn_text = self.page.locator("button:has-text('确定')").first
                    if confirm_btn_text.count() > 0:
                        logger.info("通过文本匹配找到确定按钮，点击确认...")
                        confirm_btn_text.click(timeout=5000)
                        self.page.wait_for_timeout(1000)
                        logger.info(f"SKU 配对成功: {sku}")
                        return True
                    else:
                        logger.warning("未找到确定按钮")
                        self.save_debug_info("pair_no_confirm_button")
                        return False

            self.save_debug_info("pair_no_select_button")
            logger.warning(f"未找到选择按钮，SKU可能不存在: {sku}")
            # 关闭配对弹窗，避免阻挡后续操作
            self._close_pair_modal()
            return False

        except Exception as e:
            self.save_debug_info("pair_search_error")
            logger.error(f"搜索 SKU 失败: {e}")
            # 关闭配对弹窗，避免阻挡后续操作
            self._close_pair_modal()
            return False

    def _close_pair_modal(self):
        """关闭配对弹窗"""
        try:
            # 点击弹窗的关闭按钮
            close_btn = self.page.locator(".ant-modal-close").first
            if close_btn.count() > 0 and close_btn.is_visible():
                close_btn.click(force=True)
                self.page.wait_for_timeout(500)
                logger.info("关闭配对弹窗")
                return
            # 备用：按 ESC
    def process_current_order_in_detail(self, date_str: str) -> bool:
        """处理当前在详情弹窗中显示的订单"""
        try:
            # 注意：不要调用 _dismiss_overlays()，因为详情弹窗需要保持打开
            # 优化：减少等待，之前是 1000ms
            self.page.wait_for_timeout(300)

            # 从详情页提取订单信息
            platform_sku = self._extract_platform_sku_from_detail()
            sku_info = parse_platform_sku(platform_sku) if platform_sku else None

            # 从详情页提取名称
            name1 = self._extract_name_from_detail("Name 1")
            name2 = self._extract_name_from_detail("Name 2")

            # 如果 Name 1 为空，尝试 Name Engraving（单 SKU 场景）
            if not name1:
                name1 = self._extract_name_from_detail("Name Engraving")
                # 单个刻字场景没有 Name 2，保持为空

            logger.info(f"当前订单: SKU={platform_sku}, Name1={name1}, Name2={name2}")

            if not self._detail_context_ready():
                logger.warning("详情弹窗未就绪，跳过审核与配对")
                return False

            # 检查是否已配对
            if self.is_order_paired():
                logger.info("订单已配对，跳过")
                return True

            # 未配对订单处理
            logger.info("订单未配对，开始配对流程")

            # 检查是否为 engraved 订单
            if sku_info and sku_info["custom_type"] != "engraved":
                logger.info("非定制订单，跳过配对")
                return True

            if not name1:
                logger.warning("缺少 Name1，无法配对")
                self.save_debug_info("detail_missing_name1")
                return False

            # 点击配对商品SKU链接
            if not self.click_pair_sku_button():
                logger.warning("点击配对链接失败")
                return False

            # 生成新 SKU
            if sku_info:
                # 先生成单个SKU
                single_sku = generate_single_sku(
                    sku_info["product_code"],
                    date_str,
                    name1,
                    name2
                )

                # 再生成组合SKU
                combo_sku = generate_combo_sku(
                    single_sku,
                    sku_info["card_code"],
                    sku_info["box_type"]
                )

                logger.info(f"生成单个 SKU: {single_sku}")
                logger.info(f"生成组合 SKU: {combo_sku}")

                # 只使用组合SKU进行配对（不降级）
                logger.info("尝试配对组合 SKU...")
                if self.search_and_select_sku(combo_sku):
                    logger.info("✅ 组合 SKU 配对成功")
                    self.page.wait_for_timeout(1000)
                    # 注意：不自动点击审核，让用户手动审核
                    return True
                else:
                    logger.error("❌ 组合 SKU 配对失败 - 请检查店小秘系统中是否存在该组合SKU")
                    logger.error(f"   未找到的组合SKU: {combo_sku}")
                    return False
            else:
                logger.warning("无法解析 SKU 信息")
                return False

        except Exception as e:
            logger.error(f"处理订单失败: {e}")
            self.save_debug_info("process_order_error")
            return False

    def pair_single_order(self, order_info: dict, date_str: str) -> bool:
        """配对单个订单（从列表页进入）"""
        order_no = order_info["order_no"]
        platform_sku = order_info["platform_sku"]
        row_element = order_info.get("row_element")
        row_id = order_info.get("row_id")
        # 从列表页提取的名称
        name1 = order_info.get("name1", "")
        name2 = order_info.get("name2", "")

        logger.info(f"处理订单: {order_no}")
        logger.info(f"  SKU: {platform_sku}")
        logger.info(f"  Name1: {name1}, Name2: {name2}")

        # 检查是否已处理
        if order_no in self.progress["processed_orders"]:
            logger.info(f"订单已处理，跳过: {order_no}")
            return True

        # 打开订单详情
        if not self.open_order_detail(order_no, row_element, row_id):
            return False

        self.page.wait_for_timeout(1000)

        if not self._detail_context_ready():
            logger.warning("详情弹窗未就绪，跳过审核与配对")
            return False

        # 检查是否已配对
        if self.is_order_paired():
            logger.info("订单已配对，跳过")
            self.progress["processed_orders"].append(order_no)
            save_progress(self.progress)
            return True

        # 未配对订单处理
        logger.info("订单未配对，开始配对流程")

        # 解析 SKU
        sku_info = parse_platform_sku(platform_sku) if platform_sku else None
        if not sku_info:
            platform_sku = self._extract_platform_sku_from_detail()
            sku_info = parse_platform_sku(platform_sku)

        # 只处理 engraved 订单
        if sku_info and sku_info["custom_type"] != "engraved":
            logger.info("非定制订单，跳过配对")
            self.progress["processed_orders"].append(order_no)
            save_progress(self.progress)
            return True

        # 获取名称（如果列表页没有）
        if not name1:
            name1 = self._extract_name_from_detail("Name 1")
            name2 = self._extract_name_from_detail("Name 2")

            # Fallback: 单 SKU 场景使用 Name Engraving
            if not name1:
                name1 = self._extract_name_from_detail("Name Engraving")

        if not name1:
            self.save_debug_info("detail_missing_name1")
            logger.warning(f"订单 {order_no} 缺少 Name1")
            return False

        logger.info(f"使用名称: Name1={name1}, Name2={name2}")

        # 点击配对商品SKU链接
        if not self.click_pair_sku_button():
            return False

        # 生成新 SKU
        if not sku_info:
            logger.warning("无法解析 SKU 信息")
            return False

        new_sku = generate_single_sku(
            sku_info["product_code"],
            date_str,
            name1,
            name2
        )
        logger.info(f"生成 SKU: {new_sku}")

        # 搜索并配对
        if self.search_and_select_sku(new_sku):
            logger.info("SKU 配对成功")
            self.page.wait_for_timeout(1000)
            # 注意：不自动点击审核，让用户手动审核
            self.progress["processed_orders"].append(order_no)
            save_progress(self.progress)
            return True

        return False

    def _extract_name_from_detail(self, field_name: str) -> str:
        """从订单详情弹窗中提取字段值（只从弹窗内提取，不是整个页面）"""
        try:
            # 首先获取详情弹窗容器
            detail_container = self._get_detail_container()

            # 如果找到弹窗容器，只从容器内提取
            if detail_container:
                container_text = detail_container.inner_text()
                value = self._extract_label_value_from_text(container_text, field_name)
                if value:
                    logger.debug(f"从详情弹窗提取到 {field_name}: {value}")
                    return value

            # 备用：尝试从可见的弹窗中提取
            modals = self.page.locator(".ant-modal-body, .modal-body, dialog").all()
            for modal in modals:
                try:
                    if modal.is_visible():
                        modal_text = modal.inner_text()
                        value = self._extract_label_value_from_text(modal_text, field_name)
                        if value:
                            logger.debug(f"从弹窗提取到 {field_name}: {value}")
                            return value
                except Exception:
                    continue

        except Exception as e:
            logger.debug(f"提取 {field_name} 失败: {e}")

        return ""

    def _extract_label_value_from_text(self, text: str, field_name: str) -> str:
        """从纯文本中按标签提取值"""
        label_map = {
            "Name 1": ["Name 1", "Name1", "name 1", "name1", "Text 1", "text 1", "Line 1", "line 1", "刻字1", "刻字 1", "定制1", "定制 1"],
            "Name 2": ["Name 2", "Name2", "name 2", "name2", "Text 2", "text 2", "Line 2", "line 2", "刻字2", "刻字 2", "定制2", "定制 2"],
            "Name Engraving": ["Name Engraving", "name engraving", "Engraving Name", "engraving name", "Name engraving", "刻字", "定制名"],
        }
        labels = label_map.get(field_name, [field_name])
        lines = [line.strip() for line in text.splitlines() if line.strip()]

        for label in labels:
            pattern = re.compile(rf"{re.escape(label)}\s*[:：]\s*([^\r\n]+)")
            for line in lines:
                match = pattern.search(line)
                if match:
                    return match.group(1).strip()

            # 支持标签与值分行的情况
            for idx, line in enumerate(lines[:-1]):
                if line == label:
                    return lines[idx + 1].strip()

        return ""

    def _extract_platform_sku_from_detail(self) -> str:
        """从订单详情弹窗中提取平台 SKU（只从弹窗内提取，不是整个页面）"""
        try:
            self.page.wait_for_timeout(500)

            # 首先获取详情弹窗容器
            detail_container = self._get_detail_container()

            candidates = []

            # 如果找到弹窗容器，只从容器内提取
            if detail_container:
                # 尝试从 .order-sku__meta 元素提取
                meta_elements = detail_container.locator(".order-sku__meta").all()
                for el in meta_elements:
                    try:
                        meta_text = el.inner_text()
                        candidates.extend(re.findall(r"[A-Z]\d{2,}-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", meta_text))
                    except Exception:
                        continue

                # 如果没找到，从整个弹窗文本提取
                if not candidates:
                    container_text = detail_container.inner_text()
                    candidates = re.findall(r"[A-Z]\d{2,}-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", container_text)

            # 备用：尝试从可见的弹窗中提取
            if not candidates:
                modals = self.page.locator(".ant-modal-body, .modal-body, dialog").all()
                for modal in modals:
                    try:
                        if modal.is_visible():
                            modal_text = modal.inner_text()
                            candidates = re.findall(r"[A-Z]\d{2,}-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", modal_text)
                            if candidates:
                                break
                    except Exception:
                        continue

            # 优先返回包含 engraved 的 SKU
            engraved_candidates = [c for c in candidates if "engraved" in c.lower()]
            for candidate in engraved_candidates + candidates:
                candidate = candidate.strip()
                if parse_platform_sku(candidate):
                    logger.debug(f"从详情弹窗提取到 SKU: {candidate}")
                    return candidate

        except Exception as e:
            logger.debug(f"提取平台 SKU 失败: {e}")

        return ""

    def _extract_order_no_from_detail(self) -> str:
        """从订单详情弹窗中提取平台订单号（如 5261219-59178）"""
        try:
            detail_container = self._get_detail_container()
            if not detail_container:
                return ""

            # 平台订单号通常在 .orderBagInfo 或标题区域
            # 格式如: 5261219-59178
            container_text = detail_container.inner_text()

            # 提取平台订单号（数字-数字格式）
            order_no_matches = re.findall(r'\b(\d{5,}-\d{4,})\b', container_text)
            if order_no_matches:
                logger.debug(f"从详情弹窗提取到平台订单号: {order_no_matches[0]}")
                return order_no_matches[0]

            # 尝试其他格式
            order_no_matches = re.findall(r'\b(\d{7,})\b', container_text)
            if order_no_matches:
                # 过滤掉可能是日期或其他数字的
                for match in order_no_matches:
                    if len(match) >= 8:  # 订单号一般较长
                        logger.debug(f"从详情弹窗提取到订单号: {match}")
                        return match

        except Exception as e:
            logger.debug(f"提取平台订单号失败: {e}")

        return ""

    def run_pairing(self, max_orders: int = 10, date_str: str = None, stop_order_no: str = None):
        """运行自动配对流程

        Args:
            max_orders: 最大处理订单数
            date_str: 日期字符串 (MMDD)
            stop_order_no: 截止订单号（平台订单号），处理到该订单后停止（包含该订单）
        """
        if not date_str:
            date_str = datetime.now().strftime("%m%d")

        logger.info("=" * 50)
        logger.info("开始自动配对流程")
        logger.info(f"日期: {date_str}")
        logger.info(f"最大处理数量: {max_orders}")
        if stop_order_no:
            logger.info(f"截止订单号: {stop_order_no}")
        else:
            logger.info("截止订单号: 无（处理全部）")
        logger.info("=" * 50)

        # 启动浏览器
        self.start_browser()

        try:
            # 导航到订单页面
            self.navigate_to_orders()

            # 检查登录状态
            if not self.check_login_status():
                logger.warning("未登录，需要手动登录")
                self.wait_for_login()
                self.save_auth_state()

            # 筛选未配对订单
            self.filter_unpaired_orders()

            # 获取订单列表
            orders = self.get_order_list()

            if not orders:
                logger.info("没有找到未配对订单")
                return

            # 处理订单
            success_count = 0
            fail_count = 0

            # 打开第一个订单详情
            if orders:
                first_order = orders[0]
                logger.info(f"\n打开第一个订单详情: {first_order['order_no']}")
                if not self.open_order_detail(
                    first_order["order_no"],
                    first_order.get("row_element"),
                    first_order.get("row_id")
                ):
                    logger.error("无法打开第一个订单详情")
                    return

                self.page.wait_for_timeout(1500)

            # 在详情弹窗中循环处理订单
            reached_stop_order = False
            for i in range(max_orders):
                logger.info(f"\n{'='*30}")
                logger.info(f"处理进度: {i + 1}/{max_orders}")
                logger.info(f"{'='*30}")

                # 只在指定了截止订单号时才提取当前订单号（性能优化）
                current_order_no = ""
                if stop_order_no:
                    current_order_no = self._extract_order_no_from_detail()
                    if current_order_no:
                        logger.info(f"当前平台订单号: {current_order_no}")

                    # 检查是否到达截止订单
                    if current_order_no and (stop_order_no in current_order_no or current_order_no in stop_order_no):
                        logger.info(f"🏁 到达截止订单: {current_order_no}")
                        reached_stop_order = True

                try:
                    # 处理当前订单
                    if self.process_current_order_in_detail(date_str):
                        success_count += 1
                    else:
                        fail_count += 1
                except Exception as e:
                    logger.error(f"处理订单失败: {e}")
                    self.save_debug_info(f"order_error_{i}")
                    fail_count += 1

                # 如果已到达截止订单，处理完后停止
                if reached_stop_order:
                    logger.info("🏁 已处理完截止订单，停止配对")
                    break

                # 点击"下一个"继续处理
                self.page.wait_for_timeout(500)
                if i < max_orders - 1:
                    if not self.click_next_order():
                        logger.warning("无法切换到下一个订单，结束处理")
                        break
                    self.page.wait_for_timeout(1000)

            # 关闭详情弹窗
            try:
                close_btn = self.page.locator("button:has-text('关闭')").first
                if close_btn.count() > 0:
                    close_btn.click()
            except:
                pass

            # 打印统计
            logger.info("\n" + "=" * 50)
            logger.info("配对完成!")
            logger.info(f"成功: {success_count}")
            logger.info(f"失败: {fail_count}")
            logger.info("=" * 50)

        except Exception as e:
            logger.error(f"运行出错: {e}")
            # 保存截图用于调试
            self.page.screenshot(path=str(PROJECT_ROOT / "logs" / "error_screenshot.png"))
            raise
        finally:
            self.close()


def save_auth_mode():
    """保存登录状态模式"""
    logger.info("进入保存登录状态模式...")
    logger.info("请在打开的浏览器中登录店小秘")

    config = load_config()
    automation = DianXiaoMiAutomation(
        headless=False,
        slow_mo=config["browser"].get("slow_mo", 100)
    )

    automation.start_browser()

    try:
        # 直接访问店小秘首页，会自动跳转到登录
        base_url = config["dianxiaomi"]["base_url"].rstrip("/")
        automation.page.goto(f"{base_url}/home.htm")

        logger.info("请在浏览器中完成登录...")
        logger.info("脚本会自动检测登录成功并保存状态")

        # 自动检测登录成功（等待跳转到非登录页面）
        max_wait = 300  # 最多等待5分钟
        check_interval = 2
        waited = 0

        while waited < max_wait:
            current_url = automation.page.url
            # 检查是否已登录成功（进入后台页面）
            # 登录成功后会跳转到 /web/ 开头的页面或停留在 home.htm
            if "dianxiaomi.com" in current_url and ("/web/" in current_url or "/home.htm" in current_url):
                logger.info("检测到登录成功!")
                break
            # 或者检查页面上是否有登录后的元素
            try:
                if automation.page.locator(".layout-main, .main-content, .user-info, .header-user").count() > 0:
                    logger.info("检测到已登录元素!")
                    break
            except:
                pass
            time.sleep(check_interval)
            waited += check_interval
            if waited % 10 == 0:
                logger.info(f"等待登录中... ({waited}秒)")

        if waited >= max_wait:
            logger.warning("等待登录超时")
            return

        automation.save_auth_state()
        logger.info("登录状态已保存!")

    finally:
        automation.close()


def main():
    parser = argparse.ArgumentParser(description="店小秘 SKU 自动配对脚本")
    parser.add_argument(
        "--save-auth",
        action="store_true",
        help="保存登录状态模式"
    )
    parser.add_argument(
        "--date",
        default=datetime.now().strftime("%m%d"),
        help="日期字符串，格式 MMDD，默认为今天"
    )
    parser.add_argument(
        "--max-orders",
        type=int,
        default=10,
        help="最大处理订单数，默认 10"
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="无头模式运行"
    )

    args = parser.parse_args()

    # 确保日志目录存在
    (PROJECT_ROOT / "logs").mkdir(exist_ok=True)

    if args.save_auth:
        save_auth_mode()
    else:
        # 交互式询问截止订单号
        print("\n" + "=" * 50)
        print("店小秘 SKU 自动配对脚本")
        print("=" * 50)
        print("\n请输入截止订单号（平台订单号，如 5261219-59178）")
        print("- 输入订单号: 处理到该订单后停止（包含该订单）")
        print("- 直接回车: 处理全部未配对订单")
        stop_order_no = input("\n截止订单号: ").strip()

        # 确定最大处理数量
        if stop_order_no:
            # 指定了截止订单号时，设置足够大的处理数量
            max_orders = 500
            print(f"\n✅ 将处理到订单 {stop_order_no} 为止（包含该订单）")
        else:
            max_orders = args.max_orders
            print(f"\n✅ 将处理全部未配对订单（最多 {max_orders} 个）")

        print("\n开始执行...\n")

        config = load_config()
        automation = DianXiaoMiAutomation(
            headless=args.headless,
            slow_mo=config["browser"].get("slow_mo", 100)
        )
        automation.run_pairing(
            max_orders=max_orders,
            date_str=args.date,
            stop_order_no=stop_order_no if stop_order_no else None
        )


if __name__ == "__main__":
    main()
