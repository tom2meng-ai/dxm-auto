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

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent

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
STORE_NAME = "Michael"
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


def load_card_mapping() -> dict:
    """加载卡片对应表"""
    config_path = PROJECT_ROOT / "config" / "card_mapping.json"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            mapping = json.load(f)
            mapping.pop("_comment", None)
            return mapping
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


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


def parse_platform_sku(sku: str) -> Optional[dict]:
    """解析平台 SKU"""
    if not sku or not isinstance(sku, str):
        return None

    def _detail_context_ready(self) -> bool:
        """判断详情弹窗是否可用"""
        if self._get_detail_container():
            return True
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
        return False

    parts = sku.split("-")
    if len(parts) < 3:
        return None

    result = {
        "product_code": parts[0],
        "color": parts[1] if len(parts) > 1 else "",
        "custom_type": "",
        "card_code": "",
        "box_type": "whitebox",
        "original_sku": sku
    }

    for i, part in enumerate(parts[2:], start=2):
        part_lower = part.lower()
        if part_lower == "engraved":
            result["custom_type"] = "engraved"
        elif part_lower in ("whitebox", "ledbox", "led"):
            result["box_type"] = "ledbox" if "led" in part_lower else "whitebox"
        elif i == len(parts) - 2 and result["custom_type"]:
            result["card_code"] = part
        elif not result["card_code"] and part_lower not in ("whitebox", "ledbox", "led"):
            result["card_code"] = part

    return result


def generate_single_sku(product_code: str, date_str: str, name1: str, name2: str = "") -> str:
    """生成单个 SKU"""
    names = f"{name1}+{name2}" if name2 else name1
    return f"{STORE_NAME}-{product_code}-{date_str}-{names}"


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

            # 保存调试信息
            self.save_debug_info("before_filter")

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
                    self.save_debug_info("after_filter")
                    logger.info("筛选完成")
                    break

                self.page.wait_for_timeout(1000)

            if not clicked:
                logger.warning("未找到'未配对SKU'筛选选项")
                self.save_debug_info("filter_not_found")

        except PlaywrightTimeout:
            logger.warning("筛选超时，可能没有未配对订单")
            self.save_debug_info("filter_timeout")

    def get_order_list(self) -> list:
        """获取订单列表"""
        orders = []
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

            # 备用：从包含“详情”的行中提取
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

        except PlaywrightTimeout:
            logger.warning("获取订单列表超时")
        except Exception as e:
            logger.error(f"获取订单列表出错: {e}")

        return orders

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
            current_url = self.page.url

            def _detail_visible() -> bool:
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
                start = time.time()
                while (time.time() - start) * 1000 < timeout_ms:
                    if _detail_visible():
                        return True
                    self.page.wait_for_timeout(300)
                return False

            def _try_click_detail(clickable):
                try:
                    with self.page.expect_popup(timeout=3000) as popup_info:
                        clickable.click(timeout=5000, force=True)
                    new_page = popup_info.value
                    new_page.wait_for_load_state("domcontentloaded")
                    self.page = new_page
                    return _wait_detail_visible()
                except PlaywrightTimeout:
                    clickable.click(timeout=5000, force=True)
                    return _wait_detail_visible()
                except Exception:
                    clickable.click(timeout=5000, force=True)
                    return _wait_detail_visible()

            def _click_detail_in_row(row_loc) -> bool:
                try:
                    row_loc.scroll_into_view_if_needed()
                    try:
                        row_loc.locator(".vxe-body--column.col_17").first.hover()
                    except Exception:
                        row_loc.hover()
                    action_links = row_loc.locator(".actionButton a")
                    logger.info(f"actionButton links in row: {action_links.count()}")
                    if action_links.count() > 0:
                        for i in range(action_links.count()):
                            try:
                                action_links.nth(i).click(timeout=3000, force=True)
                                if _wait_detail_visible():
                                    return True
                            except Exception:
                                continue
                    detail = row_loc.locator(
                        ".vxe-body--column.col_17 .actionButton a:nth-child(3)"
                    ).first
                    logger.info(f"detail link count in row: {detail.count()}")
                    if detail.count() > 0:
                        detail.click(timeout=5000, force=True)
                        return _wait_detail_visible()
                except Exception:
                    pass
                try:
                    clicked = row_loc.evaluate(
                        """
                        (row) => {
                          const el = row.querySelector('.vxe-body--column.col_17 .actionButton a:nth-child(3)');
                          if (!el) return false;
                          el.click();
                          return true;
                        }
                        """
                    )
                    if clicked:
                        return _wait_detail_visible()
                except Exception:
                    pass
                return False

            if row_id:
                logger.info(f"尝试使用 row_id 打开详情: {row_id}")
            if row_id:
                row_locator = self.page.locator(f"tr[rowid='{row_id}']").first
                if row_locator.count() > 0:
                    self._dismiss_overlays()
                    if _click_detail_in_row(row_locator):
                        return True
                    row_action = self.page.locator(
                        f"[rowid='{row_id}'] .actionButton a:nth-child(3)"
                    ).first
                    if row_action.count() > 0:
                        if _try_click_detail(row_action):
                            return True
                    detail_btn = row_locator.locator(
                        ".actionButton a:nth-child(3), a:has-text('详情'), span:has-text('详情'), button:has-text('详情')"
                    ).first
                    if detail_btn.count() > 0:
                        if _try_click_detail(detail_btn):
                            return True
                        self.page.wait_for_timeout(800)
                        self._dismiss_overlays()
                        if _wait_detail_visible():
                            return True

            row_by_order = self.page.locator("tr", has=self.page.locator(f"text={order_no}")).first
            if row_by_order.count() > 0:
                self._dismiss_overlays()
                if _click_detail_in_row(row_by_order):
                    return True
                order_action = self.page.locator(
                    f".vxe-body--row:has-text('{order_no}') .actionButton a:nth-child(3)"
                ).first
                if order_action.count() > 0:
                    if _try_click_detail(order_action):
                        return True
                detail_btn = row_by_order.locator(".actionButton a").nth(2)
                if detail_btn.count() == 0:
                    detail_btn = row_by_order.locator(
                        ".actionButton a:nth-child(3), a:has-text('详情'), span:has-text('详情'), button:has-text('详情')"
                    ).first
                if detail_btn.count() > 0:
                    if _try_click_detail(detail_btn):
                        return True
                    self.page.wait_for_timeout(800)
                    self._dismiss_overlays()
                    if self.page.url != current_url:
                        return _wait_detail_visible()
                    if _wait_detail_visible():
                        return True

            if row_element:
                try:
                    self.page.evaluate("el => el.scrollIntoView({block: 'center'})", row_element)
                except Exception:
                    pass
                self._dismiss_overlays()
                try:
                    row_element.hover()
                except Exception:
                    pass
                try:
                    detail_handle = row_element.query_selector(
                        ".actionButton a:nth-child(3), a:has-text('详情'), span:has-text('详情'), button:has-text('详情')"
                    )
                    if detail_handle:
                        detail_handle.click(timeout=5000)
                        if _wait_detail_visible():
                            return True
                except Exception:
                    pass

                detail_btn = self.page.locator("a:has-text('详情'), span:has-text('详情'), button:has-text('详情')").first
                if detail_btn.count() > 0:
                    if _try_click_detail(detail_btn):
                        return True
                    self.page.wait_for_timeout(800)
                    self._dismiss_overlays()
                    if self.page.url != current_url:
                        return _wait_detail_visible()
                    return _wait_detail_visible()

            # 方法1：点击"详情"按钮
            detail_btn = self.page.locator("a:has-text('详情'), span:has-text('详情'), button:has-text('详情')").first
            if detail_btn.count() > 0:
                self._dismiss_overlays()
                if _try_click_detail(detail_btn):
                    return True
                self.page.wait_for_timeout(800)
                self._dismiss_overlays()
                if self.page.url != current_url:
                    return _wait_detail_visible()
                if _wait_detail_visible():
                    return True

            # 方法2：点击订单号
            order_link = self.page.locator(f"text={order_no}").first
            if order_link.count() > 0:
                if _try_click_detail(order_link):
                    return True
                self.page.wait_for_timeout(800)
                self._dismiss_overlays()
                if self.page.url != current_url:
                    return _wait_detail_visible()
                if _wait_detail_visible():
                    return True
            self.save_debug_info("detail_button_not_found")

        except PlaywrightTimeout:
            logger.error(f"打开订单详情失败: {order_no}")
            self.save_debug_info("detail_open_timeout")

        return False

    def click_pair_sku_button(self):
        """点击配对商品SKU链接"""
        logger.info("点击配对商品SKU链接...")

        try:
            self._dismiss_overlays()
            self.page.wait_for_timeout(500)

            detail_container = self._get_detail_container()
            if detail_container:
                pair_link = detail_container.locator(
                    "a:has-text('配对商品SKU'), a:has-text('配对商品SKU'), button:has-text('配对商品SKU'), text=配对商品SKU"
                ).first
                if pair_link.count() > 0:
                    pair_link.scroll_into_view_if_needed()
                    self.page.wait_for_timeout(300)
                    pair_link.click(timeout=5000, force=True)
                    self.page.wait_for_timeout(1500)
                    logger.info("配对商品SKU链接点击成功 (detail dialog)")
                    return True
                pair_cell = detail_container.locator(
                    "td:has-text('配对商品SKU'), td:has-text('配对商品SKU'), .vxe-body--column:has-text('配对商品SKU')"
                ).first
                if pair_cell.count() > 0:
                    pair_cell.scroll_into_view_if_needed()
                    self.page.wait_for_timeout(300)
                    pair_cell.click(timeout=5000, force=True)
                    self.page.wait_for_timeout(1500)
                    logger.info("配对商品SKU链接点击成功 (detail cell)")
                    return True
                logger.warning("详情弹窗未找到配对商品SKU，可能已配对")
                return False

            for frame in self.page.frames:
                try:
                    frame_link = frame.locator(
                        "a:has-text('配对商品SKU'), a:has-text('配对商品SKU'), button:has-text('配对商品SKU'), text=配对商品SKU"
                    ).first
                    if frame_link.count() > 0 and frame_link.is_visible():
                        frame_link.scroll_into_view_if_needed()
                        frame_link.click(timeout=5000, force=True)
                        self.page.wait_for_timeout(1500)
                        logger.info("配对商品SKU链接点击成功 (frame)")
                        return True
                except Exception:
                    continue

            # 保存当前状态用于调试
            self.save_debug_info("before_click_pair_button")

            # 方法1: 检查是否在详情弹窗中，直接查找所有包含"配对"的链接
            all_links = self.page.query_selector_all("a, button, span")
            pair_links = []
            for el in all_links:
                try:
                    text = el.inner_text().strip()
                    if "配对" in text or "pair" in text.lower():
                        pair_links.append((el, text))
                except Exception:
                    continue

            logger.info(f"找到 {len(pair_links)} 个包含'配对'的元素")
            # 记录所有配对相关元素
            for el, text in pair_links:
                try:
                    visible = el.is_visible()
                    logger.info(f"配对元素: '{text}' (visible: {visible})")
                except Exception:
                    logger.info(f"配对元素: '{text}'")

            for el, text in pair_links:
                try:
                    logger.info(f"尝试点击: '{text}'")
                    el.scroll_into_view_if_needed()
                    self.page.wait_for_timeout(200)
                    el.click(timeout=3000, force=True)
                    self.page.wait_for_timeout(1000)
                    # 检查是否弹出搜索对话框
                    search_elements = self.page.query_selector_all("input[placeholder*='搜索'], input[name*='search'], .ant-modal input")
                    if search_elements:
                        logger.info(f"配对商品SKU链接点击成功 (通过文本匹配: {text})")
                        return True
                except Exception as e:
                    logger.debug(f"点击失败: {e}")
                    continue

            # 方法2: 使用更宽泛的选择器
            broad_selectors = [
                "a:has-text('配对')",
                "button:has-text('配对')",
                "span:has-text('配对')",
                "div:has-text('配对')",
                "[onclick*='pair']",
                "[data-action*='pair']"
            ]

            for selector in broad_selectors:
                links = self.page.query_selector_all(selector)
                for link in links:
                    try:
                        link.scroll_into_view_if_needed()
                        self.page.wait_for_timeout(200)
                        link.click(timeout=3000, force=True)
                        self.page.wait_for_timeout(1000)
                        # 检查是否弹出搜索框
                        search_input = self.page.locator("input").first
                        if search_input.count() > 0 and search_input.is_visible():
                            logger.info(f"配对商品SKU链接点击成功 (selector: {selector})")
                            return True
                    except Exception:
                        continue

            # 方法3: 使用JavaScript查找并点击
            clicked = self.page.evaluate("""
                () => {
                    const elements = document.querySelectorAll('a, button, span, div');
                    for (const el of elements) {
                        const text = el.innerText || el.textContent || '';
                        if (text.includes('配对商品SKU') || (text.includes('配对') && text.includes('SKU'))) {
                            el.scrollIntoView({ block: 'center' });
                            el.click();
                            return true;
                        }
                    }
                    // 更宽泛的匹配
                    for (const el of elements) {
                        const text = el.innerText || el.textContent || '';
                        if (text.includes('配对') && !text.includes('已配对')) {
                            el.scrollIntoView({ block: 'center' });
                            el.click();
                            return true;
                        }
                    }
                    return false;
                }
            """)

            if clicked:
                self.page.wait_for_timeout(2000)
                # 检查弹窗是否出现
                modal_appeared = False
                modal_selectors = [".ant-modal", ".modal", "dialog", ".ant-modal-wrap"]
                for sel in modal_selectors:
                    if self.page.locator(sel).count() > 0:
                        modal_appeared = True
                        logger.info(f"检测到弹窗出现: {sel}")
                        break
                if modal_appeared:
                    logger.info("通过JS点击配对商品SKU链接成功")
                    return True
                else:
                    logger.warning("点击后未检测到弹窗，可能需要更长时间等待")
                    self.page.wait_for_timeout(3000)
                    for sel in modal_selectors:
                        if self.page.locator(sel).count() > 0:
                            modal_appeared = True
                            logger.info(f"延迟后检测到弹窗: {sel}")
                            break
                    if modal_appeared:
                        logger.info("通过JS点击配对商品SKU链接成功")
                        return True
                    else:
                        logger.warning("点击后仍未检测到弹窗")
                        return False

            # 方法4: 如果弹窗已经打开，查找弹窗内的按钮
            modal_selectors = [
                ".ant-modal a:has-text('配对')",
                ".modal a:has-text('配对')",
                "dialog a:has-text('配对')"
            ]
            for selector in modal_selectors:
                try:
                    link = self.page.locator(selector).first
                    if link.count() > 0 and link.is_visible():
                        link.click(timeout=3000)
                        self.page.wait_for_timeout(1000)
                        logger.info(f"配对商品SKU链接点击成功 (modal selector: {selector})")
                        return True
                except Exception:
                    continue

            self.save_debug_info("pair_button_not_found")
            logger.warning("未找到配对商品SKU链接")
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
        """点击下一个按钮"""
        logger.info("点击下一个按钮...")
        try:
            self._dismiss_overlays()

            # 尝试多种方式点击"下一个"按钮
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
                        # 使用 force=True 强制点击，忽略遮挡
                        btn.click(timeout=3000, force=True)
                        self.page.wait_for_timeout(1500)
                        logger.info("切换到下一个订单")
                        return True
                    except Exception:
                        continue

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
            if clicked:
                self.page.wait_for_timeout(1500)
                logger.info("通过JS切换到下一个订单")
                return True

            logger.warning("未找到下一个按钮")
        except Exception as e:
            logger.error(f"点击下一个按钮失败: {e}")
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

    def search_and_select_sku(self, sku: str) -> bool:
        """搜索并选择 SKU"""
        logger.info(f"搜索 SKU: {sku}")

        try:
            # 等待配对弹窗加载 - 增加等待时间
            logger.info("等待配对弹窗加载...")
            self.page.wait_for_timeout(3000)
            self.save_debug_info("pair_search_start")

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
                self.page.wait_for_timeout(1000)
                logger.info(f"SKU 配对成功: {sku}")
                return True

            self.save_debug_info("pair_no_select_button")
            logger.warning(f"未找到选择按钮，SKU可能不存在: {sku}")
            return False

        except Exception as e:
            self.save_debug_info("pair_search_error")
            logger.error(f"搜索 SKU 失败: {e}")
            return False

    def process_current_order_in_detail(self, date_str: str) -> bool:
        """处理当前在详情弹窗中显示的订单"""
        try:
            self._dismiss_overlays()
            self.page.wait_for_timeout(1000)

            # 从详情页提取订单信息
            platform_sku = self._extract_platform_sku_from_detail()
            sku_info = parse_platform_sku(platform_sku) if platform_sku else None

            # 从详情页提取名称
            name1 = self._extract_name_from_detail("Name 1")
            name2 = self._extract_name_from_detail("Name 2")

            logger.info(f"当前订单: SKU={platform_sku}, Name1={name1}, Name2={name2}")

            if not self._detail_context_ready():
                logger.warning("详情弹窗未就绪，跳过审核与配对")
                return False

            # 检查是否已配对
            if self.is_order_paired():
                logger.info("订单已配对，直接审核")
                self.click_review_button()
                return True

            # 未配对订单处理
            logger.info("订单未配对，开始配对流程")

            # 检查是否为 engraved 订单
            if sku_info and sku_info["custom_type"] != "engraved":
                logger.info("非定制订单，跳过配对")
                if not self._detail_context_ready():
                    logger.warning("详情弹窗未就绪，跳过审核")
                    return False
                self.click_review_button()
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
                    # 配对成功后点击审核
                    self.click_review_button()
                    return True
                else:
                    logger.warning("SKU 配对失败")
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

        self.save_debug_info("detail_opened")
        self.page.wait_for_timeout(1000)

        if not self._detail_context_ready():
            logger.warning("详情弹窗未就绪，跳过审核与配对")
            return False

        # 检查是否已配对
        if self.is_order_paired():
            logger.info("订单已配对，直接审核")
            self.click_review_button()
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
            if not self._detail_context_ready():
                logger.warning("详情弹窗未就绪，跳过审核")
                return False
            self.click_review_button()
            self.progress["processed_orders"].append(order_no)
            save_progress(self.progress)
            return True

        # 获取名称（如果列表页没有）
        if not name1:
            name1 = self._extract_name_from_detail("Name 1")
            name2 = self._extract_name_from_detail("Name 2")

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
            # 配对成功后点击审核
            self.click_review_button()
            self.progress["processed_orders"].append(order_no)
            save_progress(self.progress)
            return True

        return False

    def _extract_name_from_detail(self, field_name: str) -> str:
        """从订单详情中提取字段值"""
        try:
            # 尝试多种定位方式
            patterns = [
                f"text={field_name}:",
                f"text={field_name}：",
                f"[data-field='{field_name}']"
            ]

            for pattern in patterns:
                el = self.page.locator(pattern).first
                if el.count() > 0:
                    text = el.inner_text()
                    # 提取冒号后的值
                    if ":" in text or "：" in text:
                        return text.split(":", 1)[-1].split("：", 1)[-1].strip()
                    return text.strip()

            # 从输入框中提取（若页面为表单展示）
            inputs = self.page.query_selector_all("input, textarea")
            for el in inputs:
                placeholder = (el.get_attribute("placeholder") or "").strip()
                aria_label = (el.get_attribute("aria-label") or "").strip()
                if field_name in placeholder or field_name in aria_label:
                    value = (el.input_value() or "").strip()
                    if value:
                        return value

            # 兜底：从页面文本中提取
            page_text = self.page.inner_text("body")
            value = self._extract_label_value_from_text(page_text, field_name)
            if value:
                return value

        except Exception as e:
            logger.debug(f"提取 {field_name} 失败: {e}")

        return ""

    def _extract_label_value_from_text(self, text: str, field_name: str) -> str:
        """从纯文本中按标签提取值"""
        label_map = {
            "Name 1": ["Name 1", "Name1", "name 1", "name1", "Text 1", "text 1", "Line 1", "line 1", "刻字1", "刻字 1", "定制1", "定制 1"],
            "Name 2": ["Name 2", "Name2", "name 2", "name2", "Text 2", "text 2", "Line 2", "line 2", "刻字2", "刻字 2", "定制2", "定制 2"],
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
        """从订单详情中提取平台 SKU"""
        try:
            self.page.wait_for_timeout(1000)
            candidates = []
            meta_elements = self.page.query_selector_all(".order-sku__meta")
            for el in meta_elements:
                meta_text = el.inner_text()
                candidates.extend(re.findall(r"[A-Z]\d{2,}-[A-Z]-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", meta_text))

            if not candidates:
                text = self.page.inner_text("body")
                candidates = re.findall(r"[A-Z]\d{2,}-[A-Z]-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", text)

            engraved_candidates = [c for c in candidates if "engraved" in c.lower()]
            for candidate in engraved_candidates + candidates:
                candidate = candidate.strip()
                if parse_platform_sku(candidate):
                    return candidate
        except Exception as e:
            logger.debug(f"提取平台 SKU 失败: {e}")

        return ""

    def run_pairing(self, max_orders: int = 10, date_str: str = None):
        """运行自动配对流程"""
        if not date_str:
            date_str = datetime.now().strftime("%m%d")

        logger.info("=" * 50)
        logger.info("开始自动配对流程")
        logger.info(f"日期: {date_str}")
        logger.info(f"最大处理数量: {max_orders}")
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
            for i in range(max_orders):
                logger.info(f"\n{'='*30}")
                logger.info(f"处理进度: {i + 1}/{max_orders}")
                logger.info(f"{'='*30}")

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
        config = load_config()
        automation = DianXiaoMiAutomation(
            headless=args.headless,
            slow_mo=config["browser"].get("slow_mo", 100)
        )
        automation.run_pairing(
            max_orders=args.max_orders,
            date_str=args.date
        )


if __name__ == "__main__":
    main()
