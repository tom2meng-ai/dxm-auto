#!/usr/bin/env python3
"""
SKU 工具模块 - 共享逻辑

包含 SKU 解析、生成等核心业务逻辑，供各脚本统一调用。
"""

import json
import re
from pathlib import Path
from typing import Optional

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent

# 常量配置
STORE_NAME = "Michael"
RED_BOX_SKU = "Michael-RED BOX"
DEFAULT_CATEGORY_ID = "1422034"
DEFAULT_WEIGHT = 60
DEFAULT_PURCHASE_PRICE = 1
DEFAULT_DECLARE_AMOUNT = 12
DEFAULT_PURCHASER = "露露"
DEFAULT_DEVELOPER = "露露"
DEFAULT_SALES_TYPE = "售卖品"
DEFAULT_LENGTH = 5
DEFAULT_WIDTH = 5
DEFAULT_HEIGHT = 5
DEFAULT_MATERIAL = "不锈钢"
DEFAULT_EN_DECLARE_NAME = "woman bracelet"
DEFAULT_CN_DECLARE_NAME = "女士手链"

# 颜色映射
COLOR_MAP = {
    "G": "金色",
    "S": "银色",
    "B": "黑色",
    "R": "玫瑰金",
}

# 产品编号到中文名称的映射
PRODUCT_NAME_MAP = {
    "J20": "爱心双扣项链",
    "J02": "环环相扣项链",
    "J01": "镂空镶钻爱心手链",
    "B09": "不锈钢皮革手链",
    "B11": "编织皮革手链",
}

# 产品类型到报关名的映射
DECLARE_NAME_MAP = {
    "J": {"en": "Necklace", "cn": "项链"},
    "B": {"en": "Bracelet", "cn": "手链"},
}


def load_card_mapping(config_path: Path = None) -> dict:
    """加载卡片对应表

    Args:
        config_path: 配置文件路径，默认为 config/card_mapping.json

    Returns:
        卡片代码到SKU的映射字典
    """
    if config_path is None:
        config_path = PROJECT_ROOT / "config" / "card_mapping.json"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            mapping = json.load(f)
            mapping.pop("_comment", None)
            return mapping
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def extract_card_code_smart(parts: list, known_cards: set) -> tuple:
    """智能提取卡片代码

    Args:
        parts: SKU 分割后的各部分
        known_cards: 已知卡片代码集合

    Returns:
        (card_code, confidence, message)
        confidence: 'high' | 'medium' | 'low'
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
        # 使用 startswith 匹配盒子类型（处理 LEDx1, whiteboxx1 等）
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


def parse_platform_sku(sku: str, card_mapping: dict = None) -> Optional[dict]:
    """解析平台 SKU

    支持多种格式：
    - 标准: B09-B-engraved-MAN10-whitebox
    - 带尺寸: B09-L-B-Engraved-MAN10-whiteboxx1
    - LED盒: B09-B-Engraved-MAN10-LEDx1

    Args:
        sku: 平台 SKU 字符串
        card_mapping: 卡片映射表，如果不传则自动加载

    Returns:
        解析后的字典，包含 product_code, color, custom_type, card_code, box_type 等
    """
    if not sku or not isinstance(sku, str):
        return None

    parts = sku.split("-")
    if len(parts) < 3:
        return None

    # 加载已知卡片代码
    if card_mapping is None:
        card_mapping = load_card_mapping()
    known_cards = set(card_mapping.keys())

    result = {
        "product_code": parts[0],
        "color": "",
        "custom_type": "",
        "card_code": "",
        "box_type": "whitebox",
        "original_sku": sku,
        "card_confidence": "",
        "parse_message": ""
    }

    # 识别 engraved 和 box_type
    for part in parts:
        part_lower = part.lower()
        if part_lower == "engraved":
            result["custom_type"] = "engraved"
        elif part_lower.startswith("led"):
            result["box_type"] = "ledbox"
        elif part_lower.startswith("whitebox"):
            result["box_type"] = "whitebox"

    # 使用智能提取卡片代码
    card_code, confidence, message = extract_card_code_smart(parts, known_cards)
    result["card_code"] = card_code
    result["card_confidence"] = confidence
    result["parse_message"] = message

    # 提取颜色（在 engraved 之前的单字母）
    for i, part in enumerate(parts[1:], 1):
        if part.lower() == "engraved":
            break
        if len(part) == 1 and part.isalpha() and part.upper() in ("B", "G", "S", "R"):
            result["color"] = part
            break

    return result


def parse_product_spec(spec: str) -> dict:
    """解析产品规格

    示例:
        Variants:Gold
        Name 1:Xaviar
        Name 2:Suzi
        _cl_options:cljhgyefn2ay

    Returns:
        {'variants': 'Gold', 'name1': 'Xaviar', 'name2': 'Suzi', 'name3': '', 'has_dual_name_format': True}
    """
    result = {
        "variants": "",
        "name1": "",
        "name2": "",
        "name3": "",
        "name4": "",
        "name5": "",
        "name6": "",
        "has_dual_name_format": False,  # 是否使用双名字格式(Name 1/Name 2)
    }

    if not spec or not isinstance(spec, str):
        return result

    for line in spec.split("\n"):
        line = line.strip()
        if ":" in line:
            key, value = line.split(":", 1)
            key = key.strip().lower()
            value = value.strip()

            if key == "variants":
                result["variants"] = value
            elif key == "name 1":
                result["name1"] = value
                result["has_dual_name_format"] = True  # 标记为双名字格式
            elif key == "name 2":
                result["name2"] = value
                result["has_dual_name_format"] = True  # 标记为双名字格式
            elif key == "name 3":
                result["name3"] = value
            elif key == "name 4":
                result["name4"] = value
            elif key == "name 5":
                result["name5"] = value
            elif key == "name 6":
                result["name6"] = value
            elif key == "name engraving":
                # 单个名字的情况
                result["name1"] = value
            elif key == "name":
                # 兼容只有 Name: 的格式
                result["name1"] = value

    return result


def validate_name_format(name: str) -> tuple:
    """验证名字只包含英文字母、数字、空格和连字符

    Args:
        name: 要验证的名字

    Returns:
        (is_valid, invalid_chars) - 是否有效，无效字符集合
    """
    if not name:
        return True, set()

    # 只允许 a-z, A-Z, 0-9, 空格和连字符
    if re.match(r'^[a-zA-Z0-9 \-]+$', name):
        return True, set()

    # 找出无效字符（排除空格和连字符）
    invalid_chars = {c for c in name if c not in ' -' and (not c.isalnum() or ord(c) > 127)}
    return False, invalid_chars


def validate_name2_required(spec_info: dict) -> tuple:
    """检查双名字格式时 Name2 是否为空

    Args:
        spec_info: parse_product_spec() 的返回结果

    Returns:
        (is_valid, error_message)
    """
    if spec_info.get("has_dual_name_format", False):
        if not spec_info.get("name2", "").strip():
            return False, "订单使用双名字格式(Name 1/Name 2)，但 Name 2 为空"
    return True, ""


def generate_single_sku(product_code: str, date_str: str, *names) -> str:
    """生成单个 SKU

    格式: Michael-{产品编号}-{MMDD}-{Name1}+{Name2}+...
    示例: Michael-J20-0121-Xaviar+Suzi+Tom

    Args:
        product_code: 产品编号
        date_str: 日期字符串 (MMDD)
        *names: 可变数量的名字 (name1, name2, name3, ...)
    """
    names_str = "+".join([n for n in names if n])  # 过滤空名字，用+连接
    return f"{STORE_NAME}-{product_code}-{date_str}-{names_str}"


def generate_single_sku_unique(product_code: str, date_str: str,
                               order_no: str, sku_counter: dict, *names) -> str:
    """生成唯一SKU，重复时自动添加订单号后缀

    Args:
        product_code: 产品编号
        date_str: 日期字符串 (MMDD)
        order_no: 完整订单号
        sku_counter: SKU计数器字典
        *names: 可变数量的名字 (name1, name2, name3, ...)

    Returns:
        唯一的SKU字符串
    """
    names_str = "+".join([n for n in names if n])  # 过滤空名字，用+连接
    base_sku = f"{STORE_NAME}-{product_code}-{date_str}-{names_str}"

    # 检测重复
    if base_sku not in sku_counter:
        sku_counter[base_sku] = 1
        return base_sku

    # 添加订单号后缀
    order_suffix = order_no.split('-')[-1]
    unique_sku = f"{base_sku}-{order_suffix}"

    return unique_sku


def generate_combo_sku(single_sku: str, card_code: str, box_type: str) -> str:
    """生成组合 SKU

    格式: {单个SKU}-{卡片代码}-{盒子类型简写}
    示例: Michael-J20-0121-Xaviar+Suzi-D17-WH

    Args:
        single_sku: 单个SKU
        card_code: 卡片代码
        box_type: 盒子类型（whitebox 或 ledbox）

    Returns:
        组合SKU字符串
    """
    box_short = "LED" if "led" in box_type.lower() else "WH"
    return f"{single_sku}-{card_code}-{box_short}"


def generate_identifier(order_no: str, product_code: str, name1: str) -> str:
    """生成识别码: 订单后5位-产品编号-完整Name

    Examples:
        >>> generate_identifier("5261219-59178", "J20", "Jonathan")
        "59178-J20-Jonathan"
    """
    order_suffix = order_no.split('-')[-1][-5:]
    name_full = name1 if name1 else ""
    return f"{order_suffix}-{product_code}-{name_full}"


def get_chinese_name(product_code: str, color: str, name1: str, name2: str) -> str:
    """生成中文名称"""
    product_name = PRODUCT_NAME_MAP.get(product_code, product_code)
    color_cn = COLOR_MAP.get(color.upper(), color)
    names = f"{name1}+{name2}" if name2 else name1
    return f"{STORE_NAME}-{product_name}-{color_cn}-{names}"


def get_declare_names(product_code: str) -> tuple:
    """获取报关名称（英文，中文）"""
    prefix = product_code[0].upper() if product_code else ""
    if prefix in DECLARE_NAME_MAP:
        return DECLARE_NAME_MAP[prefix]["en"], DECLARE_NAME_MAP[prefix]["cn"]
    return "Jewelry", "饰品"


# Excel 必填列名
REQUIRED_COLUMNS = ["SKU", "订单号", "产品规格"]


def validate_excel_columns(df, required_columns: list = None) -> tuple:
    """校验 Excel 文件是否包含必填列

    Args:
        df: pandas DataFrame
        required_columns: 必填列名列表，默认使用 REQUIRED_COLUMNS

    Returns:
        (is_valid, missing_columns, message)
        - is_valid: 是否通过校验
        - missing_columns: 缺失的列名列表
        - message: 错误信息（如果有）
    """
    if required_columns is None:
        required_columns = REQUIRED_COLUMNS

    existing_columns = set(df.columns)
    missing_columns = [col for col in required_columns if col not in existing_columns]

    if missing_columns:
        message = f"Excel 文件缺少必填列: {', '.join(missing_columns)}。请检查文件格式是否正确。"
        return False, missing_columns, message

    return True, [], ""

