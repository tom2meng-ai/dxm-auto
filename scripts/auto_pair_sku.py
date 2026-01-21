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

            # 保存调试信息
            self.save_debug_info("before_filter")

            # 尝试多种方式点击"未配对SKU"
            clicked = False

            # 方法1: 使用正则匹配
            unpaired_link = self.page.locator("text=/未配对SKU/")
            if unpaired_link.count() > 0:
                unpaired_link.first.click()
                clicked = True
                logger.info("方法1成功: text=/未配对SKU/")

            # 方法2: 精确文本匹配（带数字）
            if not clicked:
                links = self.page.locator("a, span")
                count = links.count()
                for i in range(count):
                    try:
                        text = links.nth(i).inner_text()
                        if "未配对SKU" in text:
                            links.nth(i).click()
                            clicked = True
                            logger.info(f"方法2成功: 找到文本 '{text}'")
                            break
                    except:
                        continue

            # 方法3: 通过包含关键字的元素
            if not clicked:
                selectors = [
                    "a:has-text('未配对')",
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
                    except:
                        continue

            if clicked:
                self.page.wait_for_timeout(2000)
                self.page.wait_for_load_state("networkidle")
                self.save_debug_info("after_filter")
                logger.info("筛选完成")
            else:
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

            if order_no:
                return {
                    "order_no": order_no,
                    "platform_sku": platform_sku,
                    "row_element": row
                }
        except Exception as e:
            logger.debug(f"提取订单信息失败: {e}")

        return None

    def open_order_detail(self, order_no: str, row_element=None):
        """打开订单详情"""
        logger.info(f"打开订单详情: {order_no}")

        try:
            if row_element:
                detail_btn = row_element.query_selector("a:has-text('详情'), span:has-text('详情'), button:has-text('详情')")
                if detail_btn:
                    detail_btn.click()
                    self.page.wait_for_timeout(500)
                    return True

            # 方法1：点击"详情"按钮
            detail_btn = self.page.locator(f"text=详情").first
            if detail_btn:
                detail_btn.click()
                self.page.wait_for_timeout(500)
                return True

            # 方法2：点击订单号
            order_link = self.page.locator(f"text={order_no}").first
            if order_link:
                order_link.click()
                self.page.wait_for_timeout(500)
                return True

        except PlaywrightTimeout:
            logger.error(f"打开订单详情失败: {order_no}")

        return False

    def click_pair_sku_button(self):
        """点击配对商品SKU按钮"""
        logger.info("点击配对商品SKU按钮...")

        try:
            # 多种可能的按钮文本
            possible_texts = ["配对商品SKU", "配对SKU", "配对", "手动匹配"]

            for text in possible_texts:
                btn = self.page.locator(f"text={text}").first
                if btn.count() > 0:
                    btn.click()
                    self.page.wait_for_timeout(1000)
                    return True

            logger.warning("未找到配对按钮")
        except Exception as e:
            logger.error(f"点击配对按钮失败: {e}")

        return False

    def search_and_select_sku(self, sku: str) -> bool:
        """搜索并选择 SKU"""
        logger.info(f"搜索 SKU: {sku}")

        try:
            # 等待结果表格出现
            self.page.wait_for_timeout(500)
            self.page.wait_for_selector(".order-sku__meta, text=选择", timeout=8000)

            # 先尝试直接匹配结果列表中的 SKU
            direct_match = self.page.locator(".order-sku__meta", has_text=sku).first
            if direct_match.count() > 0:
                row = direct_match.locator("xpath=ancestor::tr[1]")
                select_btn = row.locator("button:has-text('选择'), text=选择").first
                if select_btn.count() > 0:
                    select_btn.click()
                    self.page.wait_for_timeout(500)
                    logger.info(f"SKU 配对成功: {sku}")
                    return True

            # 等待搜索框出现
            search_selectors = [
                "input[placeholder*='搜索']",
                "input[placeholder*='SKU']",
                ".search-input input",
                ".ant-modal input",
                ".el-dialog input"
            ]
            search_input = None
            for selector in search_selectors:
                try:
                    search_input = self.page.wait_for_selector(selector, timeout=5000)
                    if search_input:
                        break
                except PlaywrightTimeout:
                    continue

            if not search_input:
                self.save_debug_info("pair_search_input_not_found")
                logger.warning("未找到 SKU 搜索输入框")
                return False

            # 清空并输入 SKU
            search_input.fill("")
            search_input.fill(sku)
            self.page.wait_for_timeout(300)

            # 点击搜索按钮或回车
            search_clicked = False
            search_buttons = [
                "button:has-text('搜索')",
                "text=搜索",
                ".ant-modal button:has-text('搜索')",
                ".el-dialog button:has-text('搜索')"
            ]
            for btn_selector in search_buttons:
                btn = self.page.locator(btn_selector).first
                if btn.count() > 0:
                    btn.click()
                    search_clicked = True
                    break
            if not search_clicked:
                search_input.press("Enter")

            self.page.wait_for_load_state("networkidle")
            self.page.wait_for_timeout(1000)

            # 检查搜索结果并选择（优先匹配 SKU）
            direct_match = self.page.locator(".order-sku__meta", has_text=sku).first
            if direct_match.count() > 0:
                row = direct_match.locator("xpath=ancestor::tr[1]")
                select_btn = row.locator("button:has-text('选择'), text=选择").first
                if select_btn.count() > 0:
                    select_btn.click()
                    self.page.wait_for_timeout(500)
                    logger.info(f"SKU 配对成功: {sku}")
                    return True

            select_btn = self.page.locator("text=选择, button:has-text('选择')").first
            if select_btn.count() > 0:
                select_btn.click()
                self.page.wait_for_timeout(500)
                logger.info(f"SKU 配对成功: {sku}")
                return True

            # 尝试点击表格第一行
            row_selectors = [
                "table tbody tr",
                ".ant-table-row",
                ".el-table__row"
            ]
            for row_selector in row_selectors:
                row = self.page.locator(row_selector).first
                if row.count() > 0:
                    row.click()
                    self.page.wait_for_timeout(300)
                    confirm_btn = self.page.locator("text=确定, button:has-text('确定')").first
                    if confirm_btn.count() > 0:
                        confirm_btn.click()
                        self.page.wait_for_timeout(500)
                        logger.info(f"SKU 配对成功: {sku}")
                        return True

            self.save_debug_info("pair_search_no_result")
            logger.warning(f"未找到 SKU: {sku}")
            return False

        except PlaywrightTimeout:
            self.save_debug_info("pair_search_timeout")
            logger.error(f"搜索 SKU 超时: {sku}")
            return False

    def pair_single_order(self, order_info: dict, date_str: str) -> bool:
        """配对单个订单"""
        order_no = order_info["order_no"]
        platform_sku = order_info["platform_sku"]
        row_element = order_info.get("row_element")

        logger.info(f"处理订单: {order_no}")

        # 检查是否已处理
        if order_no in self.progress["processed_orders"]:
            logger.info(f"订单已处理，跳过: {order_no}")
            return True

        detail_opened = False

        # 解析 SKU，如列表页无 SKU 则进入详情页提取
        sku_info = parse_platform_sku(platform_sku) if platform_sku else None
        if not sku_info:
            if not self.open_order_detail(order_no, row_element):
                return False
            detail_opened = True
            platform_sku = self._extract_platform_sku_from_detail()
            if platform_sku:
                logger.info(f"解析平台SKU: {platform_sku}")
            sku_info = parse_platform_sku(platform_sku)

        if not sku_info:
            logger.warning(f"无法解析 SKU: {platform_sku}")
            return False

        logger.info(f"判定平台SKU: {platform_sku} | custom_type: {sku_info['custom_type']}")

        # 只处理 engraved 订单
        if sku_info["custom_type"] != "engraved":
            logger.info(f"非定制订单，跳过: {order_no}")
            return True

        # 打开订单详情
        if not detail_opened:
            if not self.open_order_detail(order_no, row_element):
                return False

        # 点击配对按钮
        if not self.click_pair_sku_button():
            return False

        # TODO: 这里需要从订单详情中提取 Name1, Name2
        # 目前使用占位符，实际使用时需要根据页面结构调整
        name1 = self._extract_name_from_detail("Name 1")
        name2 = self._extract_name_from_detail("Name 2")

        if not name1:
            self.save_debug_info("detail_missing_name1")
            logger.warning(f"订单 {order_no} 缺少 Name1")
            return False

        # 生成新 SKU
        new_sku = generate_single_sku(
            sku_info["product_code"],
            date_str,
            name1,
            name2
        )

        # 搜索并配对
        if self.search_and_select_sku(new_sku):
            # 记录成功
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

            for i, order in enumerate(orders[:max_orders]):
                logger.info(f"\n处理进度: {i + 1}/{min(len(orders), max_orders)}")

                try:
                    if self.pair_single_order(order, date_str):
                        success_count += 1
                    else:
                        fail_count += 1
                except Exception as e:
                    logger.error(f"处理订单失败: {e}")
                    fail_count += 1

                # 添加延迟避免操作过快
                self.page.wait_for_timeout(500)

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
