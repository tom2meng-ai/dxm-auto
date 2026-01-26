#!/usr/bin/env python3
"""
店小秘 SKU 生成器 - Web 界面

启动方式: python web_app.py
访问地址: http://localhost:5000
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template, request, send_file, jsonify
import pandas as pd

app = Flask(__name__, template_folder='templates')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max

# 项目根目录
PROJECT_ROOT = Path(__file__).parent

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


def parse_platform_sku(sku: str) -> dict:
    """解析平台 SKU"""
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
        "color": parts[1] if len(parts) > 1 else "",
        "custom_type": "",
        "card_code": "",
        "box_type": "whitebox",
        "original_sku": sku,
        "card_confidence": "",  # 新增：置信度
        "parse_message": ""     # 新增：解析消息
    }

    # 识别 engraved 和 box_type
    for i, part in enumerate(parts[2:], start=2):
        part_lower = part.lower()
        if part_lower == "engraved":
            result["custom_type"] = "engraved"
        elif part_lower in ("whitebox", "ledbox", "led"):
            result["box_type"] = "ledbox" if "led" in part_lower else "whitebox"

    # 使用智能提取卡片代码
    card_code, confidence, message = extract_card_code_smart(parts, known_cards)
    result["card_code"] = card_code
    result["card_confidence"] = confidence
    result["parse_message"] = message

    return result


def parse_product_spec(spec: str) -> dict:
    """解析产品规格"""
    result = {"variants": "", "name1": "", "name2": "", "name3": ""}

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
            elif key == "name 2":
                result["name2"] = value
            elif key == "name 3":
                result["name3"] = value
            elif key == "name engraving":
                # 单个名字的情况：Name Engraving: xxx
                result["name1"] = value

    return result


def extract_card_code_smart(parts: list, known_cards: set) -> tuple:
    """智能提取卡片代码

    Args:
        parts: SKU 分割后的各部分
        known_cards: 已知卡片代码集合

    Returns:
        (card_code, confidence, message)
        confidence: 'high' | 'medium' | 'low'
    """
    # 黑名单：已知无意义字符
    NOISE_CHARS = {"X", "SM", "SB", "B", "G", "S", "R"}
    BOX_KEYWORDS = {"whitebox", "ledbox", "led"}

    # 找到 engraved 的位置
    engraved_idx = -1
    box_idx = len(parts)

    for i, part in enumerate(parts):
        if part.lower() == "engraved":
            engraved_idx = i
        if part.lower() in BOX_KEYWORDS:
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

    # 优先级 2: 过滤噪音，选择最可能的
    filtered = [c for c in candidates if c not in NOISE_CHARS and len(c) >= 2]

    if filtered:
        card_code = filtered[0]  # 取第一个符合条件的
        return card_code, "medium", f"基于规则提取: {card_code}"

    # 优先级 3: 兜底 - 取第一个非噪音字符
    for candidate in candidates:
        if candidate not in NOISE_CHARS:
            return candidate, "low", f"兜底规则提取: {candidate}（可能不准确）"

    return "", "low", "无法提取卡片代码"


def generate_single_sku_unique(product_code: str, date_str: str, name1: str, name2: str,
                                order_no: str, sku_counter: dict) -> str:
    """生成唯一SKU，重复时自动添加订单号后缀

    Args:
        product_code: 产品编号
        date_str: 日期字符串 (MMDD)
        name1: 第一个名字
        name2: 第二个名字（可选）
        order_no: 完整订单号
        sku_counter: SKU计数器字典

    Returns:
        唯一的SKU字符串
    """
    names = f"{name1}+{name2}" if name2 else name1
    base_sku = f"{STORE_NAME}-{product_code}-{date_str}-{names}"

    # 检测重复
    if base_sku not in sku_counter:
        sku_counter[base_sku] = 1
        return base_sku

    # 添加订单号后缀
    order_suffix = order_no.split('-')[-1]
    unique_sku = f"{base_sku}-{order_suffix}"

    return unique_sku


def generate_identifier(order_no: str, product_code: str, name1: str) -> str:
    """生成识别码: 订单后5位-产品编号-完整Name

    Examples:
        >>> generate_identifier("5261219-59178", "J20", "Jonathan")
        "59178-J20-Jonathan"
        >>> generate_identifier("5261219-59178", "B09", "Sarah")
        "59178-B09-Sarah"
    """
    # 提取订单号后5位
    order_suffix = order_no.split('-')[-1][-5:]

    # 使用完整的Name1
    name_full = name1 if name1 else ""

    return f"{order_suffix}-{product_code}-{name_full}"


def generate_combo_sku(single_sku: str, card_code: str, box_type: str) -> str:
    """生成组合 SKU"""
    box_short = "LED" if "led" in box_type.lower() else "WH"
    return f"{single_sku}-{card_code}-{box_short}"


def get_chinese_name(product_code: str, color: str, name1: str, name2: str) -> str:
    """生成中文名称"""
    product_name = PRODUCT_NAME_MAP.get(product_code, product_code)
    color_cn = COLOR_MAP.get(color.upper(), color)
    names = f"{name1}+{name2}" if name2 else name1
    return f"{STORE_NAME}-{product_name}-{color_cn}-{names}"


def get_declare_names(product_code: str) -> tuple:
    """获取报关名称"""
    prefix = product_code[0].upper() if product_code else ""
    if prefix in DECLARE_NAME_MAP:
        return DECLARE_NAME_MAP[prefix]["en"], DECLARE_NAME_MAP[prefix]["cn"]
    return "Jewelry", "饰品"


def get_image_url_for_order(df: pd.DataFrame, order_no: str, current_url: str) -> str:
    """获取订单的图片URL

    优先使用当前行的URL,如果没有则查找同订单号其他行

    Args:
        df: 原始订单DataFrame
        order_no: 订单号
        current_url: 当前行的图片URL

    Returns:
        图片URL字符串
    """
    # 如果当前行有URL,直接使用
    if current_url and str(current_url) != 'nan' and str(current_url).strip():
        return current_url

    # 否则,查找同订单号的其他行
    order_rows = df[df["订单号"] == order_no]

    for idx, row in order_rows.iterrows():
        url = row.get("产品图片网址", "")  # 更新：使用产品图片网址
        if url and str(url) != 'nan' and str(url).strip():
            return url

    # 整个订单都没有URL
    return ""


def process_orders(df: pd.DataFrame, date_str: str) -> tuple:
    """处理订单数据

    Args:
        df: 完整的订单DataFrame（包含所有行）
        date_str: 日期字符串 (MMDD)

    Returns:
        (single_df, combo_df, logs, error_df)
    """
    # 保存原始完整DataFrame（用于查找图片URL）
    df_original = df.copy()
    total_rows = len(df)

    # 存储结果
    card_mapping = load_card_mapping()
    single_sku_rows = []
    combo_sku_rows = []
    logs = []
    error_rows = []

    # 唯一性检测器
    sku_counter = {}  # SKU重复检测
    identifier_set = set()  # 识别码重复检测

    # 处理非定制订单（记录到错误报告）
    df_non_engraved = df[~df["SKU"].str.contains("engraved", case=False, na=False)]
    logs.append(f"📊 输入文件总行数: {total_rows}")
    logs.append(f"📊 非定制订单数: {len(df_non_engraved)}")

    for idx, row in df_non_engraved.iterrows():
        order_no = row.get("订单号", "")
        platform_sku = row.get("SKU", "")
        error_rows.append({
            "订单号": order_no,
            "平台SKU": platform_sku,
            "错误类型": "非定制订单",
            "错误详情": "该订单不包含 engraved 关键词，属于非定制订单",
            "产品规格": "",
            "Name1": "",
            "Name2": "",
            "解析出的产品编号": "",
            "解析出的卡片代码": "",
            "卡片置信度": "",
            "建议操作": "非定制订单无需处理SKU"
        })
        logs.append(f"⚠️ 非定制订单跳过: {order_no} - {platform_sku}")

    # 过滤 engraved 订单
    df_engraved = df[df["SKU"].str.contains("engraved", case=False, na=False)]
    logs.append(f"📊 定制订单数: {len(df_engraved)}")

    if df_engraved.empty:
        logs.append("⚠️ 没有找到定制订单")
        return pd.DataFrame(), pd.DataFrame(), logs, pd.DataFrame(error_rows)

    for idx, row in df_engraved.iterrows():
        order_no = row.get("订单号", "")
        platform_sku = row.get("SKU", "")
        product_spec = row.get("产品规格", "")
        image_url_current = row.get("产品图片网址", "")  # 更新：使用产品图片网址

        # 智能获取图片URL
        image_url = get_image_url_for_order(df_original, order_no, image_url_current)

        # 处理 SKU 中的换行符，只取第一行（平台 SKU）
        if isinstance(platform_sku, str) and "\n" in platform_sku:
            platform_sku = platform_sku.split("\n")[0].strip()

        sku_info = parse_platform_sku(platform_sku)
        spec_info = parse_product_spec(product_spec)

        # 错误处理 + 记录
        if not sku_info:
            error_rows.append({
                "订单号": order_no,
                "平台SKU": platform_sku,
                "错误类型": "SKU解析失败",
                "错误详情": "无法解析平台 SKU 格式",
                "产品规格": product_spec,
                "Name1": "",
                "Name2": "",
                "解析出的产品编号": "",
                "解析出的卡片代码": "",
                "卡片置信度": "",
                "建议操作": "请检查 SKU 格式是否正确"
            })
            logs.append(f"⚠️ 无法解析 SKU: {platform_sku}")
            continue

        if not spec_info["name1"]:
            error_rows.append({
                "订单号": order_no,
                "平台SKU": platform_sku,
                "错误类型": "缺少Name1",
                "错误详情": "产品规格中未找到 Name 1 或 Name Engraving",
                "产品规格": product_spec,
                "Name1": "",
                "Name2": spec_info.get("name2", ""),
                "解析出的产品编号": sku_info.get("product_code", ""),
                "解析出的卡片代码": sku_info.get("card_code", ""),
                "卡片置信度": sku_info.get("card_confidence", ""),
                "建议操作": "请确认产品规格中是否包含客户名字"
            })
            logs.append(f"⚠️ 缺少 Name1: 订单 {order_no}")
            continue

        product_code = sku_info["product_code"]
        color = sku_info["color"]
        card_code = sku_info["card_code"]
        box_type = sku_info["box_type"]
        name1 = spec_info["name1"]
        name2 = spec_info["name2"]

        # 检查卡片代码置信度
        if sku_info.get("card_confidence") == "low" and card_code:
            error_rows.append({
                "订单号": order_no,
                "平台SKU": platform_sku,
                "错误类型": "卡片代码识别不确定",
                "错误详情": sku_info.get("parse_message", ""),
                "产品规格": product_spec,
                "Name1": name1,
                "Name2": name2,
                "解析出的产品编号": product_code,
                "解析出的卡片代码": card_code,
                "卡片置信度": sku_info.get("card_confidence", ""),
                "建议操作": f"请确认卡片代码是否为 {card_code}"
            })
            logs.append(f"⚠️ 卡片代码识别不确定: {order_no} - {card_code}")

        # 生成唯一SKU
        single_sku = generate_single_sku_unique(
            product_code, date_str, name1, name2,
            order_no, sku_counter
        )

        # 生成识别码
        identifier = generate_identifier(order_no, product_code, name1)

        # 识别码冲突检测
        if identifier in identifier_set:
            error_rows.append({
                "订单号": order_no,
                "平台SKU": platform_sku,
                "错误类型": "识别码重复冲突",
                "错误详情": f"识别码 {identifier} 已存在（同订单、同产品、Name首字母相同）",
                "产品规格": product_spec,
                "Name1": name1,
                "Name2": name2,
                "解析出的产品编号": product_code,
                "解析出的卡片代码": card_code,
                "卡片置信度": sku_info.get("card_confidence", ""),
                "建议操作": "请检查是否为重复订单"
            })
            logs.append(f"⚠️ 识别码冲突: {identifier} (订单 {order_no})")
            continue

        identifier_set.add(identifier)

        # 生成组合SKU
        combo_sku = generate_combo_sku(single_sku, card_code, box_type)

        # 单个 SKU 记录
        single_row = {
            "*SKU\n(必填)": single_sku,
            "平台SKU": "",
            "识别码": identifier,
            "中文名称": get_chinese_name(product_code, color, name1, name2),
            "英文名称": "",
            "分类ID": DEFAULT_CATEGORY_ID,
            "图片URL\n（必须以http://或https：//开头）": image_url,
            "商品净重\n（g）": DEFAULT_WEIGHT,
            "采购参考价\n（RMB）": DEFAULT_PURCHASE_PRICE,
            "采购员\n（输入子账号姓名或名称）": DEFAULT_PURCHASER,
            "长（cm）": DEFAULT_LENGTH,
            "宽（cm）": DEFAULT_WIDTH,
            "高（cm）": DEFAULT_HEIGHT,
            "来源URL\n（必须以http://或https：//开头）": "",
            "备注": "",
            "英文报关名": DEFAULT_EN_DECLARE_NAME,
            "中文报关名": DEFAULT_CN_DECLARE_NAME,
            "申报重量\n(g)": DEFAULT_WEIGHT,
            "申报金额\n（USD）": DEFAULT_DECLARE_AMOUNT,
            "出口申报金额（USD）": "",
            "危险运输品": "",
            "材质": DEFAULT_MATERIAL,
            "用途": "",
            "海关编码": "",
            "开发员\n（输入子账号姓名或名称）": DEFAULT_DEVELOPER,
            "销售方式": DEFAULT_SALES_TYPE,
            "销售员\n（输入子账号姓名或名称）": "",
        }
        single_sku_rows.append(single_row)

        # 组合 SKU 记录 - 主商品行
        combo_main_row = {
            "*组合sku": combo_sku,
            "平台SKU": "",
            "识别码": f"{identifier}-GROUP",  # 组合SKU识别码加上-GROUP后缀
            "中文名称": f"{get_chinese_name(product_code, color, name1, name2)}-{card_code}",
            "英文名称": "",
            "分类ID": DEFAULT_CATEGORY_ID,
            "组合SKU主图URL\n（必须以http://或https：//开头）": image_url,
            "*包含的商品sku": single_sku,
            "*数量": 1,
            "长（cm）": DEFAULT_LENGTH,
            "宽（cm）": DEFAULT_WIDTH,
            "高（cm）": DEFAULT_HEIGHT,
            "来源URL(必须以http://或https://开头)": "",
            "备注": "",
            "英文报关名": DEFAULT_EN_DECLARE_NAME,
            "中文报关名": DEFAULT_CN_DECLARE_NAME,
            "申报重量(g)": DEFAULT_WEIGHT,
            "申报金额\n（USD）": DEFAULT_DECLARE_AMOUNT,
            "出口申报金额（USD）": "",
            "危险运输品": "",
            "材质": DEFAULT_MATERIAL,
            "用途": "",
            "海关编码": "",
            "销售方式": DEFAULT_SALES_TYPE,
        }
        combo_sku_rows.append(combo_main_row)

        # 卡片行
        if card_code and card_code in card_mapping:
            card_sku = card_mapping[card_code]
            combo_sku_rows.append({
                "*组合sku": combo_sku,
                "*包含的商品sku": card_sku,
                "*数量": 1,
            })
        elif card_code:
            error_rows.append({
                "订单号": order_no,
                "平台SKU": platform_sku,
                "错误类型": "卡片代码未找到",
                "错误详情": f"卡片代码 {card_code} 不在映射表中",
                "产品规格": product_spec,
                "Name1": name1,
                "Name2": name2,
                "解析出的产品编号": product_code,
                "解析出的卡片代码": card_code,
                "卡片置信度": sku_info.get("card_confidence", ""),
                "建议操作": f"请在 card_mapping.json 中添加 {card_code} 的映射"
            })
            logs.append(f"⚠️ 未找到卡片代码: {card_code}")

        # 红盒行
        if "led" in box_type.lower():
            combo_sku_rows.append({
                "*组合sku": combo_sku,
                "*包含的商品sku": RED_BOX_SKU,
                "*数量": 1,
            })

        logs.append(f"✅ {order_no} → {single_sku}")

    # 创建错误 DataFrame
    error_df = pd.DataFrame(error_rows) if error_rows else pd.DataFrame()

    return pd.DataFrame(single_sku_rows), pd.DataFrame(combo_sku_rows), logs, error_df


@app.route('/')
def index():
    """首页"""
    return render_template('index.html', today=datetime.now().strftime("%m%d"))


@app.route('/generate', methods=['POST'])
def generate():
    """生成 SKU Excel"""
    if 'file' not in request.files:
        return jsonify({'error': '请上传文件'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': '请选择文件'}), 400

    date_str = request.form.get('date', datetime.now().strftime("%m%d"))

    try:
        df = pd.read_excel(file)
        single_df, combo_df, logs, error_df = process_orders(df, date_str)

        if single_df.empty:
            return jsonify({'error': '没有找到可处理的 engraved 订单', 'logs': logs}), 400

        # 保存文件
        output_dir = PROJECT_ROOT / "data" / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        single_file = output_dir / f"output_单个SKU_{timestamp}.xlsx"
        combo_file = output_dir / f"output_组合SKU_{timestamp}.xlsx"
        error_file = output_dir / f"output_错误报告_{timestamp}.xlsx" if not error_df.empty else None

        single_df.to_excel(single_file, index=False)
        combo_df.to_excel(combo_file, index=False)

        if error_file:
            error_df.to_excel(error_file, index=False)

        response = {
            'success': True,
            'single_count': len(single_df),
            'combo_count': len(combo_df),
            'error_count': len(error_df) if not error_df.empty else 0,
            'single_file': str(single_file.name),
            'combo_file': str(combo_file.name),
            'logs': logs
        }

        if error_file:
            response['error_file'] = str(error_file.name)

        return jsonify(response)

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/download/<filename>')
def download(filename):
    """下载文件"""
    file_path = PROJECT_ROOT / "data" / "output" / filename
    if file_path.exists():
        return send_file(file_path, as_attachment=True)
    return jsonify({'error': '文件不存在'}), 404


if __name__ == '__main__':
    # 确保目录存在
    (PROJECT_ROOT / "templates").mkdir(exist_ok=True)
    (PROJECT_ROOT / "data" / "output").mkdir(parents=True, exist_ok=True)

    print("=" * 50)
    print("店小秘 SKU 生成器 - Web 界面")
    print("=" * 50)
    print("访问地址: http://localhost:8080")
    print("按 Ctrl+C 停止服务")
    print("=" * 50)

    app.run(debug=True, host='127.0.0.1', port=8080, use_reloader=False)
