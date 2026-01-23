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


def extract_card_code_smart(parts: list, known_cards: set) -> tuple:
    """智能提取卡片代码

    Args:
        parts: SKU 分割后的各部分
        known_cards: 已知卡片代码集合

    Returns:
        (card_code, confidence, message)
    """
    # 黑名单：颜色代码和无意义字符
    NOISE_CHARS = {"X", "SM", "SB", "B", "G", "S", "R", "L"}
    BOX_KEYWORDS = {"whitebox", "ledbox", "led"}

    # 找到 engraved 的位置
    engraved_idx = -1
    box_idx = len(parts)

    for i, part in enumerate(parts):
        part_lower = part.lower()
        if part_lower == "engraved":
            engraved_idx = i
        # 修复：使用 startswith 匹配盒子类型
        if any(part_lower.startswith(kw) for kw in BOX_KEYWORDS):
            box_idx = i
            break

    if engraved_idx == -1:
        return "", "low", "未找到 engraved 关键词"

    # 候选区域：engraved 之后、盒子类型之前
    candidates = parts[engraved_idx + 1:box_idx]

    if not candidates:
        return "", "low", "engraved 后没有候选卡片代码"

    # 优先级 1: 匹配已知卡片代码
    for candidate in candidates:
        if candidate in known_cards:
            return candidate, "high", f"匹配已知卡片代码: {candidate}"

    # 优先级 2: 过滤噪音，选择最可能的（长度>=2且不是颜色/尺寸代码）
    filtered = [c for c in candidates if c.upper() not in NOISE_CHARS and len(c) >= 2]

    if filtered:
        return filtered[0], "medium", f"基于规则提取: {filtered[0]}"

    # 优先级 3: 兜底
    for candidate in candidates:
        if candidate.upper() not in NOISE_CHARS:
            return candidate, "low", f"兜底提取: {candidate}"

    return "", "low", "无法提取卡片代码"


def parse_platform_sku(sku: str) -> Optional[dict]:
    """解析平台 SKU

    支持多种格式：
    - 标准: B09-B-engraved-MAN10-whitebox
    - 带尺寸: B09-L-B-Engraved-MAN10-whiteboxx1
    - LED盒: B09-B-Engraved-MAN10-LEDx1
    """
    if not sku or not isinstance(sku, str):
        return None

    parts = sku.split("-")
    if len(parts) < 3:
        return None

    # 加载已知卡片代码
    card_mapping = load_card_mapping()
    known_cards = set(card_mapping.keys())

    result = {
        "product_code": parts[0],
        "color": "",
        "custom_type": "",
        "card_code": "",
        "box_type": "whitebox",
        "original_sku": sku
    }

    # 识别 engraved 和 box_type
    for part in parts:
        part_lower = part.lower()
        if part_lower == "engraved":
            result["custom_type"] = "engraved"
        # 修复：使用 startswith 匹配盒子类型（处理 LEDx1, whiteboxx1 等）
        elif part_lower.startswith("led"):
            result["box_type"] = "ledbox"
        elif part_lower.startswith("whitebox"):
            result["box_type"] = "whitebox"

    # 使用智能提取卡片代码
    card_code, confidence, message = extract_card_code_smart(parts, known_cards)
    result["card_code"] = card_code

    # 提取颜色（在 engraved 之前的单字母）
    for i, part in enumerate(parts[1:], 1):
        if part.lower() == "engraved":
            break
        if len(part) == 1 and part.isalpha() and part.upper() in ("B", "G", "S", "R"):
            result["color"] = part
            break

    return result


def generate_single_sku(product_code: str, date_str: str, name1: str, name2: str = "") -> str:
    """生成单个 SKU"""
    names = f"{name1}+{name2}" if name2 else name1
    return f"{STORE_NAME}-{product_code}-{date_str}-{names}"


def generate_combo_sku(single_sku: str, card_code: str, box_type: str) -> str:
    """生成组合 SKU

    格式: {单个SKU}-{卡片代码}-{盒子类型简写}
    示例: Michael-J20-0121-Xaviar+Suzi-D17-WH

    Args:
        single_sku: 单个SKU（如 Michael-J20-0121-Xaviar+Suzi）
        card_code: 卡片代码（如 D17, MAN10）
        box_type: 盒子类型（whitebox 或 ledbox）

    Returns:
        组合SKU字符串
    """
    box_short = "LED" if "led" in box_type.lower() else "WH"
    return f"{single_sku}-{card_code}-{box_short}"


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
            # 注意：不调用 _dismiss_overlays()，因为此时的弹窗是订单详情弹窗，需要保留

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
            # 注意：不调用 _dismiss_overlays()，保留当前弹窗
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

            # 点击前先记录当前订单的SKU，用于后续判断是否真正切换了
            current_sku_before = self._extract_platform_sku_from_detail()

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

            # 检测是否出现"最后一个订单"的提示
            if self._is_last_order():
                logger.info("🏁 已经是最后一个订单，停止处理")
                return False

            # 检测订单是否真正切换了（SKU是否变化）
            current_sku_after = self._extract_platform_sku_from_detail()
            if current_sku_before and current_sku_after and current_sku_before == current_sku_after:
                logger.info("🏁 订单未切换（SKU相同），可能已是最后一个")
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
        """搜索并选择 SKU（使用精确定位）"""
        logger.info(f"搜索 SKU: {sku}")

        try:
            # 等待配对弹窗加载
            logger.info("等待配对弹窗加载...")
            self.page.wait_for_timeout(2000)

            # 精确定位：在"商品搜索" dialog 中查找搜索框
            # 定位方式来自 codegen 录制
            dialog = self.page.get_by_role("dialog", name="arrow_back 返回 商品搜索")
            if dialog.count() == 0:
                # 备用：尝试查找任何包含"商品搜索"的 dialog
                dialog = self.page.locator("dialog:has-text('商品搜索')").first
                if dialog.count() == 0:
                    logger.warning("未找到商品搜索弹窗")
                    self.save_debug_info("pair_dialog_not_found")
                    return False

            # 在 dialog 中定位搜索输入框
            search_input = dialog.locator("#searchWareHoseProductsValue")
            if search_input.count() == 0:
                logger.warning("未找到搜索输入框 #searchWareHoseProductsValue")
                self.save_debug_info("pair_search_input_not_found")
                return False

            logger.info("输入SKU...")
            search_input.fill(sku)
            self.page.wait_for_timeout(500)

            # 点击搜索按钮（第3个"搜索"按钮，索引为2）
            search_btn = self.page.get_by_role("button", name="搜索").nth(2)
            if search_btn.count() > 0:
                search_btn.click()
                logger.info("点击搜索按钮")
            else:
                # 备用：直接按回车
                search_input.press("Enter")
                logger.info("按回车搜索")

            # 等待搜索结果加载
            self.page.wait_for_timeout(2000)

            # 点击"选择"按钮
            select_btn = self.page.get_by_role("button", name="选择")
            if select_btn.count() == 0:
                logger.warning(f"未找到选择按钮，SKU可能不存在: {sku}")
                self.save_debug_info("pair_no_select_button")
                self._close_pair_modal()
                return False

            select_btn.first.click()
            logger.info("点击选择按钮")
            self.page.wait_for_timeout(1000)

            # 点击"确定"按钮确认配对
            confirm_btn = self.page.get_by_role("button", name="确定")
            if confirm_btn.count() == 0:
                logger.warning("未找到确定按钮")
                self.save_debug_info("pair_no_confirm_button")
                return False

            confirm_btn.first.click()
            logger.info("点击确定按钮")
            self.page.wait_for_timeout(1500)

            logger.info(f"✅ SKU 配对成功: {sku}")
            return True

        except Exception as e:
            logger.error(f"搜索 SKU 失败: {e}")
            self.save_debug_info("pair_search_error")
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
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(500)
        except Exception:
            pass

    def process_current_order_in_detail(self, date_str: str) -> bool:
        """处理当前在详情弹窗中显示的订单（支持多SKU）"""
        try:
            # 注意：不要调用 _dismiss_overlays()，因为详情弹窗需要保持打开
            self.page.wait_for_timeout(1000)

            if not self._detail_context_ready():
                logger.warning("详情弹窗未就绪，跳过审核与配对")
                return False

            # 检查是否已配对
            if self.is_order_paired():
                logger.info("订单已配对，跳过")
                return True

            # 提取所有产品信息
            products = self._extract_all_products_from_detail()
            if not products:
                logger.warning("未找到产品信息")
                return False

            # 筛选 engraved 产品
            engraved_products = [p for p in products if "engraved" in p["sku"].lower()]
            if not engraved_products:
                logger.info("没有 engraved 产品，跳过配对")
                return True

            logger.info(f"找到 {len(engraved_products)} 个 engraved 产品")

            # 检查是否为多SKU情况（相同平台SKU，不同名字）
            # 按平台SKU分组
            sku_groups = {}
            for p in engraved_products:
                sku = p["sku"]
                if sku not in sku_groups:
                    sku_groups[sku] = []
                sku_groups[sku].append(p)

            # 找出需要多SKU处理的组（相同平台SKU有多个产品）
            multi_sku_groups = {sku: prods for sku, prods in sku_groups.items() if len(prods) > 1}

            if multi_sku_groups:
                logger.info(f"🔄 检测到多SKU订单，共 {len(multi_sku_groups)} 组需要处理")
                return self._process_multi_sku_order(multi_sku_groups, date_str)
            else:
                # 单SKU情况，使用原有逻辑
                return self._process_single_sku_order(engraved_products[0], date_str)

        except Exception as e:
            logger.error(f"处理订单失败: {e}")
            self.save_debug_info("process_order_error")
            return False

    def _process_single_sku_order(self, product: dict, date_str: str) -> bool:
        """处理单SKU订单（原有逻辑）"""
        platform_sku = product["sku"]
        name1 = product["name1"]
        name2 = product.get("name2", "")

        logger.info(f"处理单SKU订单: SKU={platform_sku}, Name1={name1}, Name2={name2}")

        sku_info = parse_platform_sku(platform_sku)
        if not sku_info:
            logger.warning("无法解析 SKU 信息")
            return False

        if not name1:
            logger.warning("缺少 Name1，无法配对")
            self.save_debug_info("detail_missing_name1")
            return False

        # 点击配对商品SKU链接
        if not self.click_pair_sku_button():
            logger.warning("点击配对链接失败")
            return False

        # 生成组合SKU
        single_sku = generate_single_sku(
            sku_info["product_code"],
            date_str,
            name1,
            name2
        )
        combo_sku = generate_combo_sku(
            single_sku,
            sku_info["card_code"],
            sku_info["box_type"]
        )

        logger.info(f"生成组合 SKU: {combo_sku}")

        # 配对
        if self.search_and_select_sku(combo_sku):
            logger.info("✅ 组合 SKU 配对成功")
            self.page.wait_for_timeout(1000)
            return True
        else:
            logger.error(f"❌ 组合 SKU 配对失败: {combo_sku}")
            return False

    def _process_multi_sku_order(self, sku_groups: dict, date_str: str) -> bool:
        """处理多SKU订单

        流程：
        1. 对每组相同平台SKU的产品，先配对第一个
        2. 然后用"编辑→追加额外商品"添加剩余的
        3. 填写数量，移除重复项，保存

        Args:
            sku_groups: {平台SKU: [产品列表]}，每组至少有2个产品
            date_str: 日期字符串
        """
        for platform_sku, products in sku_groups.items():
            logger.info(f"\n{'='*40}")
            logger.info(f"处理多SKU组: {platform_sku} ({len(products)} 个产品)")

            sku_info = parse_platform_sku(platform_sku)
            if not sku_info:
                logger.warning(f"无法解析 SKU: {platform_sku}")
                continue

            # 第一步：配对第一个产品
            first_product = products[0]
            if not first_product["name1"]:
                logger.warning("第一个产品缺少 Name1")
                continue

            first_combo_sku = generate_combo_sku(
                generate_single_sku(
                    sku_info["product_code"],
                    date_str,
                    first_product["name1"],
                    first_product.get("name2", "")
                ),
                sku_info["card_code"],
                sku_info["box_type"]
            )

            logger.info(f"第1步: 配对第一个产品 - {first_product['name1']}")
            logger.info(f"  组合SKU: {first_combo_sku}")

            # 点击配对
            if not self.click_pair_sku_button():
                logger.warning("点击配对链接失败")
                continue

            if not self.search_and_select_sku(first_combo_sku):
                logger.error(f"❌ 第一个产品配对失败: {first_combo_sku}")
                continue

            logger.info("✅ 第一个产品配对成功")
            self.page.wait_for_timeout(1500)

            # 第二步：生成剩余产品的组合SKU
            remaining_products = products[1:]
            remaining_skus = []
            for p in remaining_products:
                if not p["name1"]:
                    logger.warning(f"产品缺少 Name1，跳过")
                    continue
                combo_sku = generate_combo_sku(
                    generate_single_sku(
                        sku_info["product_code"],
                        date_str,
                        p["name1"],
                        p.get("name2", "")
                    ),
                    sku_info["card_code"],
                    sku_info["box_type"]
                )
                remaining_skus.append({
                    "combo_sku": combo_sku,
                    "quantity": p.get("quantity", 1),
                    "name1": p["name1"]
                })

            if not remaining_skus:
                logger.info("没有剩余产品需要追加")
                continue

            logger.info(f"第2步: 追加 {len(remaining_skus)} 个额外产品")

            # 第三步：追加额外商品
            if not self._append_extra_products(remaining_skus):
                logger.error("❌ 追加额外商品失败")
                continue

            # 第四步：移除重复项（数量-1个）
            remove_count = len(products) - 1
            logger.info(f"第3步: 移除 {remove_count} 个重复项")
            if not self._remove_duplicate_products(remove_count):
                logger.error("❌ 移除重复项失败")
                continue

            # 第五步：保存
            logger.info("第4步: 保存")
            if not self._save_product_changes():
                logger.error("❌ 保存失败")
                continue

            logger.info(f"✅ 多SKU组处理完成: {platform_sku}")

        return True

    def _append_extra_products(self, products_to_add: list) -> bool:
        """追加额外商品

        Args:
            products_to_add: [{"combo_sku": "xxx", "quantity": 1, "name1": "Tom"}, ...]
        """
        try:
            # 第1步：点击"编辑/追加"按钮
            logger.info("第1步: 点击编辑/追加...")
            edit_append_link = self.page.get_by_role("link", name="编辑/追加")
            if edit_append_link.count() == 0:
                logger.warning("未找到编辑/追加链接")
                self.save_debug_info("edit_append_not_found")
                return False

            edit_append_link.first.click()
            self.page.wait_for_timeout(500)

            # 第2步：悬停在"追加商品"上，显示下拉菜单
            logger.info("第2步: 悬停追加商品...")
            append_link = self.page.get_by_role("link", name="追加商品")
            if append_link.count() == 0:
                logger.warning("未找到追加商品链接")
                self.save_debug_info("append_link_not_found")
                return False

            append_link.first.hover()
            self.page.wait_for_timeout(500)

            # 第3步：点击下拉菜单中的"追加额外商品"
            logger.info("第3步: 点击追加额外商品...")
            extra_product_btn = self.page.get_by_text("追加额外商品")
            if extra_product_btn.count() == 0:
                logger.warning("未找到追加额外商品选项")
                self.save_debug_info("extra_product_not_found")
                return False

            extra_product_btn.first.click()
            self.page.wait_for_timeout(1500)

            # 依次搜索并选择每个SKU
            for item in products_to_add:
                sku = item["combo_sku"]
                logger.info(f"  搜索SKU: {sku}")

                # 输入搜索
                search_input = self.page.locator("#newSearchWareHoseProductsValue")
                if search_input.count() == 0:
                    logger.warning("未找到搜索输入框")
                    return False

                search_input.fill("")
                search_input.fill(sku)
                self.page.wait_for_timeout(500)

                # 点击搜索
                search_btn = self.page.get_by_role("button", name="搜索")
                if search_btn.count() > 0:
                    search_btn.first.click()
                else:
                    search_input.press("Enter")

                self.page.wait_for_timeout(2000)

                # 点击选择
                select_btn = self.page.get_by_role("button", name="选择").first
                if select_btn.count() > 0:
                    select_btn.click()
                    logger.info(f"  ✅ 已选择: {sku}")
                    self.page.wait_for_timeout(500)
                else:
                    logger.warning(f"  未找到选择按钮，SKU可能不存在: {sku}")

            # 点击确定选择
            logger.info("点击确定选择...")
            confirm_btn = self.page.get_by_role("button", name="确定选择")
            if confirm_btn.count() > 0:
                confirm_btn.first.click()
                self.page.wait_for_timeout(1500)
            else:
                logger.warning("未找到确定选择按钮")
                return False

            # 填写数量
            logger.info("填写数量...")
            for item in products_to_add:
                sku = item["combo_sku"]
                quantity = item["quantity"]

                # 找到对应SKU的数量输入框
                # 格式: cell name 包含 SKU 名称的一部分
                try:
                    # 尝试通过 placeholder 直接定位
                    qty_inputs = self.page.get_by_placeholder("填写数量").all()
                    for qty_input in qty_inputs:
                        try:
                            # 检查这个输入框是否属于我们要填写的SKU
                            parent_row = qty_input.locator("xpath=ancestor::tr")
                            if parent_row.count() > 0:
                                row_text = parent_row.inner_text()
                                # 用 name1 来匹配，因为 SKU 名称包含 name1
                                if item["name1"] in row_text:
                                    qty_input.fill(str(quantity))
                                    logger.info(f"  填写数量 {quantity} for {item['name1']}")
                                    break
                        except Exception:
                            continue
                except Exception as e:
                    logger.debug(f"填写数量出错: {e}")
                    # 备用：直接填写所有空的数量输入框
                    qty_inputs = self.page.get_by_placeholder("填写数量").all()
                    for qty_input in qty_inputs:
                        try:
                            current_val = qty_input.input_value()
                            if not current_val:
                                qty_input.fill(str(quantity))
                                break
                        except Exception:
                            continue

            return True

        except Exception as e:
            logger.error(f"追加额外商品失败: {e}")
            self.save_debug_info("append_extra_products_error")
            return False

    def _remove_duplicate_products(self, count: int) -> bool:
        """移除重复的商品配对

        Args:
            count: 要移除的数量
        """
        try:
            logger.info(f"移除 {count} 个重复项...")

            for i in range(count):
                # 每次移除第一个（因为移除后索引会变）
                # 或者从后往前移除
                remove_link = self.page.get_by_role("link", name="移除").first
                if remove_link.count() > 0:
                    remove_link.click()
                    logger.info(f"  移除第 {i+1} 个")
                    self.page.wait_for_timeout(500)
                else:
                    logger.warning(f"未找到移除链接，已移除 {i} 个")
                    break

            return True

        except Exception as e:
            logger.error(f"移除重复项失败: {e}")
            return False

    def _save_product_changes(self) -> bool:
        """保存商品配对更改"""
        try:
            save_link = self.page.get_by_role("link", name="保存")
            if save_link.count() > 0:
                save_link.first.click()
                self.page.wait_for_timeout(2000)
                logger.info("✅ 保存成功")
                return True
            else:
                logger.warning("未找到保存按钮")
                return False

        except Exception as e:
            logger.error(f"保存失败: {e}")
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
        """从订单详情弹窗中提取平台 SKU（只从弹窗内提取，不是整个页面）

        注意：此方法只返回第一个SKU，用于向后兼容。
        如需获取所有产品，请使用 _extract_all_products_from_detail()
        """
        products = self._extract_all_products_from_detail()
        if products:
            return products[0]["sku"]
        return ""

    def _extract_all_products_from_detail(self) -> list:
        """从订单详情弹窗中提取所有产品的SKU、名称和数量

        Returns:
            [
                {"sku": "B09-B-Engraved-MAN10-LEDx1", "name1": "John", "name2": "Mary", "quantity": 1, "index": 0},
                {"sku": "J20-G-engraved-D17-whitebox", "name1": "Tom", "name2": "Lisa", "quantity": 2, "index": 1}
            ]
        """
        products = []
        try:
            self.page.wait_for_timeout(500)

            # 首先获取详情弹窗容器
            detail_container = self._get_detail_container()
            if not detail_container:
                logger.warning("未找到详情弹窗容器")
                return products

            # 方法1: 尝试从产品区块中提取（每个产品是一个独立区块）
            # 店小秘详情页中，每个产品通常在一个 .order-sku 或类似的容器中
            product_blocks = detail_container.locator(".order-sku, .product-item, .sku-item, [class*='product'], [class*='sku-row']").all()

            if product_blocks:
                logger.info(f"找到 {len(product_blocks)} 个产品区块")
                for idx, block in enumerate(product_blocks):
                    try:
                        block_text = block.inner_text()

                        # 提取SKU
                        sku_matches = re.findall(r"[A-Z]\d{2,}-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", block_text)
                        sku = ""
                        for candidate in sku_matches:
                            if parse_platform_sku(candidate):
                                sku = candidate
                                break

                        if not sku:
                            continue

                        # 提取名称
                        name1 = self._extract_label_value_from_text(block_text, "Name 1")
                        name2 = self._extract_label_value_from_text(block_text, "Name 2")

                        # 单SKU场景的备用
                        if not name1:
                            name1 = self._extract_label_value_from_text(block_text, "Name Engraving")

                        # 提取数量（从 .order-sku__quantity 元素）
                        quantity = 1  # 默认数量为1
                        try:
                            qty_el = block.locator(".order-sku__meta > .order-sku__quantity").first
                            if qty_el.count() > 0:
                                qty_text = qty_el.inner_text().strip()
                                # 数量可能是 "1" 或 "x1" 或 "×1" 格式
                                qty_match = re.search(r'(\d+)', qty_text)
                                if qty_match:
                                    quantity = int(qty_match.group(1))
                        except Exception:
                            pass

                        products.append({
                            "sku": sku,
                            "name1": name1,
                            "name2": name2,
                            "quantity": quantity,
                            "index": idx
                        })
                        logger.debug(f"产品 {idx}: SKU={sku}, Name1={name1}, Name2={name2}, Qty={quantity}")
                    except Exception as e:
                        logger.debug(f"提取产品区块 {idx} 失败: {e}")
                        continue

            # 方法2: 如果没有找到独立区块，从整个弹窗中提取
            if not products:
                logger.info("未找到独立产品区块，从整个弹窗提取")
                container_text = detail_container.inner_text()

                # 提取所有SKU
                all_skus = re.findall(r"[A-Z]\d{2,}-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", container_text)
                valid_skus = []
                seen = set()
                for candidate in all_skus:
                    if candidate not in seen and parse_platform_sku(candidate):
                        valid_skus.append(candidate)
                        seen.add(candidate)

                # 提取所有名称（可能有多组）
                name1_values = self._extract_all_label_values_from_text(container_text, "Name 1")
                name2_values = self._extract_all_label_values_from_text(container_text, "Name 2")

                # 单SKU场景的备用
                if not name1_values:
                    name1_values = self._extract_all_label_values_from_text(container_text, "Name Engraving")

                # 提取所有数量（使用定位器）
                quantities = []
                try:
                    qty_elements = detail_container.locator(".order-sku__meta > .order-sku__quantity").all()
                    for qty_el in qty_elements:
                        try:
                            qty_text = qty_el.inner_text().strip()
                            qty_match = re.search(r'(\d+)', qty_text)
                            if qty_match:
                                quantities.append(int(qty_match.group(1)))
                            else:
                                quantities.append(1)
                        except Exception:
                            quantities.append(1)
                except Exception:
                    pass

                # 匹配SKU、名称和数量
                for idx, sku in enumerate(valid_skus):
                    name1 = name1_values[idx] if idx < len(name1_values) else ""
                    name2 = name2_values[idx] if idx < len(name2_values) else ""
                    quantity = quantities[idx] if idx < len(quantities) else 1
                    products.append({
                        "sku": sku,
                        "name1": name1,
                        "name2": name2,
                        "quantity": quantity,
                        "index": idx
                    })

            logger.info(f"共提取到 {len(products)} 个产品")
            for p in products:
                logger.info(f"  产品 {p['index']}: {p['sku']} (Name1={p['name1']}, Name2={p['name2']}, Qty={p.get('quantity', 1)})")

        except Exception as e:
            logger.error(f"提取所有产品失败: {e}")

        return products

    def _extract_all_label_values_from_text(self, text: str, field_name: str) -> list:
        """从文本中提取所有匹配的标签值（支持多个相同标签）"""
        label_map = {
            "Name 1": ["Name 1", "Name1", "name 1", "name1", "Text 1", "text 1", "Line 1", "line 1", "刻字1", "刻字 1", "定制1", "定制 1"],
            "Name 2": ["Name 2", "Name2", "name 2", "name2", "Text 2", "text 2", "Line 2", "line 2", "刻字2", "刻字 2", "定制2", "定制 2"],
            "Name Engraving": ["Name Engraving", "name engraving", "Engraving Name", "engraving name", "Name engraving", "刻字", "定制名"],
        }
        labels = label_map.get(field_name, [field_name])
        values = []

        for label in labels:
            pattern = re.compile(rf"{re.escape(label)}\s*[:：]\s*([^\r\n]+)")
            matches = pattern.findall(text)
            for match in matches:
                value = match.strip()
                if value and value not in values:
                    values.append(value)

        return values

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
