#!/usr/bin/env python3
"""
店小秘 SKU 生成器 - Web 界面

启动方式: python web_app.py
访问地址: http://localhost:5000
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template, request, send_file, jsonify
import pandas as pd

# 添加 scripts 目录到路径
sys.path.insert(0, str(Path(__file__).parent / "scripts"))

# 从共享模块导入
from sku_utils import (
    PROJECT_ROOT,
    STORE_NAME,
    RED_BOX_SKU,
    DEFAULT_CATEGORY_ID,
    DEFAULT_WEIGHT,
    DEFAULT_PURCHASE_PRICE,
    DEFAULT_DECLARE_AMOUNT,
    DEFAULT_PURCHASER,
    DEFAULT_DEVELOPER,
    DEFAULT_SALES_TYPE,
    DEFAULT_LENGTH,
    DEFAULT_WIDTH,
    DEFAULT_HEIGHT,
    DEFAULT_MATERIAL,
    DEFAULT_EN_DECLARE_NAME,
    DEFAULT_CN_DECLARE_NAME,
    COLOR_MAP,
    PRODUCT_NAME_MAP,
    load_card_mapping,
    extract_card_code_smart,
    parse_platform_sku,
    parse_product_spec,
    generate_single_sku_unique,
    generate_combo_sku,
    generate_identifier,
    get_chinese_name,
    get_declare_names,
    validate_excel_columns,
)

app = Flask(__name__, template_folder='templates')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max


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
    # 校验必填列
    is_valid, missing_cols, error_msg = validate_excel_columns(df)
    if not is_valid:
        return pd.DataFrame(), pd.DataFrame(), [f"❌ {error_msg}"], pd.DataFrame()

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

        # 传入已加载的 card_mapping 避免重复读取文件
        sku_info = parse_platform_sku(platform_sku, card_mapping)
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
