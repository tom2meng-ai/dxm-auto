#!/usr/bin/env python3
"""
店小秘 SKU 自动配对系统 - Excel 生成脚本

功能：
1. 读取店小秘导出的订单 Excel
2. 过滤 engraved（定制）订单
3. 生成单个 SKU 导入表格
4. 生成组合 SKU 导入表格

使用方法：
    python scripts/generate_sku_excel.py input.xlsx [--date MMDD]
"""

import argparse
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('logs/generate_sku.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

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

# 颜色映射
COLOR_MAP = {
    "G": "金色",
    "S": "银色",
    "B": "黑色",
    "R": "玫瑰金",
}

# 产品编号到中文名称的映射（可扩展）
PRODUCT_NAME_MAP = {
    "J20": "爱心双扣项链",
    "J02": "环环相扣项链",
    "J01": "镂空镶钻爱心手链",
    "B09": "不锈钢皮革手链",
    # 更多产品待添加
}

# 产品类型到报关名的映射
DECLARE_NAME_MAP = {
    "J": {"en": "Necklace", "cn": "项链"},  # J开头是项链
    "B": {"en": "Bracelet", "cn": "手链"},  # B开头是手链
}


def load_card_mapping() -> dict:
    """加载卡片对应表"""
    config_path = PROJECT_ROOT / "config" / "card_mapping.json"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            mapping = json.load(f)
            # 移除注释字段
            mapping.pop("_comment", None)
            return mapping
    except FileNotFoundError:
        logger.warning(f"卡片对应表未找到: {config_path}")
        return {}
    except json.JSONDecodeError as e:
        logger.error(f"卡片对应表格式错误: {e}")
        return {}


def parse_platform_sku(sku: str) -> Optional[dict]:
    """
    解析平台 SKU

    示例: J20-G-engraved-D17-whitebox
    返回: {
        'product_code': 'J20',
        'color': 'G',
        'custom_type': 'engraved',
        'card_code': 'D17',
        'box_type': 'whitebox'
    }
    """
    if not sku or not isinstance(sku, str):
        return None

    # 标准格式: {产品编号}-{颜色}-{定制类型}-{卡片代码}-{盒子类型}
    # 但有些SKU可能格式不同，需要灵活处理
    parts = sku.split("-")

    if len(parts) < 3:
        return None

    result = {
        "product_code": parts[0],
        "color": parts[1] if len(parts) > 1 else "",
        "custom_type": "",
        "card_code": "",
        "box_type": "whitebox",  # 默认白盒
        "original_sku": sku
    }

    # 查找 engraved 和盒子类型
    for i, part in enumerate(parts[2:], start=2):
        part_lower = part.lower()
        if part_lower == "engraved":
            result["custom_type"] = "engraved"
        elif part_lower in ("whitebox", "ledbox", "led"):
            result["box_type"] = "ledbox" if "led" in part_lower else "whitebox"
        elif i == len(parts) - 2 and result["custom_type"]:
            # engraved 后面的是卡片代码
            result["card_code"] = part
        elif not result["card_code"] and part_lower not in ("whitebox", "ledbox", "led"):
            # 可能是卡片代码
            result["card_code"] = part

    return result


def parse_product_spec(spec: str) -> dict:
    """
    解析产品规格

    示例:
        Variants:Gold
        Name 1:Xaviar
        Name 2:Suzi
        _cl_options:cljhgyefn2ay

    返回: {'variants': 'Gold', 'name1': 'Xaviar', 'name2': 'Suzi'}
    """
    result = {
        "variants": "",
        "name1": "",
        "name2": "",
        "name3": "",
    }

    if not spec or not isinstance(spec, str):
        return result

    # 按行解析
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

    return result


def generate_single_sku(product_code: str, date_str: str, name1: str, name2: str) -> str:
    """
    生成单个 SKU

    格式: Michael-{产品编号}-{MMDD}-{Name1}+{Name2}
    示例: Michael-J20-0121-Xaviar+Suzi
    """
    names = f"{name1}+{name2}" if name2 else name1
    return f"{STORE_NAME}-{product_code}-{date_str}-{names}"


def generate_combo_sku(single_sku: str, card_code: str, box_type: str) -> str:
    """
    生成组合 SKU

    格式: {单个SKU}-{卡片代码}-{盒子类型简写}
    示例: Michael-J20-0121-Xaviar+Suzi-D17-WH
    """
    box_short = "LED" if "led" in box_type.lower() else "WH"
    return f"{single_sku}-{card_code}-{box_short}"


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


def process_orders(input_file: str, date_str: str) -> tuple:
    """
    处理订单文件

    返回: (单个SKU DataFrame, 组合SKU DataFrame)
    """
    logger.info(f"读取输入文件: {input_file}")

    # 读取 Excel
    df = pd.read_excel(input_file)
    logger.info(f"总行数: {len(df)}")

    # 过滤 engraved 订单
    df_engraved = df[df["SKU"].str.contains("engraved", case=False, na=False)]
    logger.info(f"Engraved 订单数: {len(df_engraved)}")

    if df_engraved.empty:
        logger.warning("没有找到 engraved 订单")
        return pd.DataFrame(), pd.DataFrame()

    # 加载卡片对应表
    card_mapping = load_card_mapping()

    # 存储结果
    single_sku_rows = []
    combo_sku_rows = []

    # 处理每一行
    for idx, row in df_engraved.iterrows():
        order_no = row.get("订单号", "")
        platform_sku = row.get("SKU", "")
        product_spec = row.get("产品规格", "")

        # 解析数据
        sku_info = parse_platform_sku(platform_sku)
        spec_info = parse_product_spec(product_spec)

        if not sku_info:
            logger.warning(f"无法解析 SKU: {platform_sku}")
            continue

        if not spec_info["name1"]:
            logger.warning(f"缺少 Name1: 订单 {order_no}")
            continue

        product_code = sku_info["product_code"]
        color = sku_info["color"]
        card_code = sku_info["card_code"]
        box_type = sku_info["box_type"]
        name1 = spec_info["name1"]
        name2 = spec_info["name2"]

        # 生成 SKU
        single_sku = generate_single_sku(product_code, date_str, name1, name2)
        combo_sku = generate_combo_sku(single_sku, card_code, box_type)

        # 获取报关名
        en_declare, cn_declare = get_declare_names(product_code)

        # 单个 SKU 记录
        single_row = {
            "*SKU\n(必填)": single_sku,
            "平台SKU": platform_sku,
            "识别码": order_no,
            "中文名称": get_chinese_name(product_code, color, name1, name2),
            "英文名称": "",
            "分类ID": DEFAULT_CATEGORY_ID,
            "图片URL\n（必须以http://或https：//开头）": "",
            "商品净重\n（g）": DEFAULT_WEIGHT,
            "采购参考价\n（RMB）": DEFAULT_PURCHASE_PRICE,
            "采购员\n（输入子账号姓名或名称）": DEFAULT_PURCHASER,
            "长（cm）": "",
            "宽（cm）": "",
            "高（cm）": "",
            "来源URL\n（必须以http://或https：//开头）": "",
            "备注": "",
            "英文报关名": en_declare,
            "中文报关名": cn_declare,
            "申报重量\n(g)": DEFAULT_WEIGHT,
            "申报金额\n（USD）": DEFAULT_DECLARE_AMOUNT,
            "出口申报金额（USD）": "",
            "危险运输品": "",
            "材质": "",
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
            "平台SKU": platform_sku,
            "识别码": order_no,
            "中文名称": f"{get_chinese_name(product_code, color, name1, name2)}-{card_code}",
            "英文名称": "",
            "分类ID": DEFAULT_CATEGORY_ID,
            "组合SKU主图URL\n（必须以http://或https：//开头）": "",
            "*包含的商品sku": single_sku,
            "*数量": 1,
            "长（cm）": "",
            "宽（cm）": "",
            "高（cm）": "",
            "来源URL(必须以http://或https://开头)": "",
            "备注": "",
            "英文报关名": en_declare,
            "中文报关名": cn_declare,
            "申报重量(g)": DEFAULT_WEIGHT,
            "申报金额\n（USD）": DEFAULT_DECLARE_AMOUNT,
            "出口申报金额（USD）": "",
            "危险运输品": "",
            "材质": "",
            "用途": "",
            "海关编码": "",
            "销售方式": DEFAULT_SALES_TYPE,
        }
        combo_sku_rows.append(combo_main_row)

        # 组合 SKU 记录 - 卡片行
        if card_code and card_code in card_mapping:
            card_sku = card_mapping[card_code]
            combo_card_row = {
                "*组合sku": combo_sku,
                "*包含的商品sku": card_sku,
                "*数量": 1,
            }
            combo_sku_rows.append(combo_card_row)
        elif card_code:
            logger.warning(f"未找到卡片代码对应的 SKU: {card_code}")

        # 组合 SKU 记录 - 红盒行（如果是 LED 盒子）
        if "led" in box_type.lower():
            combo_box_row = {
                "*组合sku": combo_sku,
                "*包含的商品sku": RED_BOX_SKU,
                "*数量": 1,
            }
            combo_sku_rows.append(combo_box_row)

        logger.info(f"处理完成: {order_no} -> {single_sku}")

    # 创建 DataFrame
    single_df = pd.DataFrame(single_sku_rows)
    combo_df = pd.DataFrame(combo_sku_rows)

    return single_df, combo_df


def main():
    parser = argparse.ArgumentParser(description="店小秘 SKU Excel 生成脚本")
    parser.add_argument("input_file", help="输入的订单 Excel 文件路径")
    parser.add_argument(
        "--date",
        default=datetime.now().strftime("%m%d"),
        help="日期字符串，格式 MMDD，默认为今天"
    )
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT / "data" / "output"),
        help="输出目录，默认为 data/output"
    )

    args = parser.parse_args()

    # 确保日志目录存在
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(exist_ok=True)

    # 确保输出目录存在
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("=" * 50)
    logger.info("店小秘 SKU Excel 生成脚本启动")
    logger.info(f"输入文件: {args.input_file}")
    logger.info(f"日期: {args.date}")
    logger.info(f"输出目录: {output_dir}")
    logger.info("=" * 50)

    # 处理订单
    single_df, combo_df = process_orders(args.input_file, args.date)

    if single_df.empty:
        logger.error("没有生成任何数据")
        sys.exit(1)

    # 输出文件
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    single_output = output_dir / f"output_单个SKU_{timestamp}.xlsx"
    combo_output = output_dir / f"output_组合SKU_{timestamp}.xlsx"

    # 保存 Excel
    single_df.to_excel(single_output, index=False)
    combo_df.to_excel(combo_output, index=False)

    logger.info(f"单个 SKU 文件已生成: {single_output} ({len(single_df)} 条)")
    logger.info(f"组合 SKU 文件已生成: {combo_output} ({len(combo_df)} 条)")
    logger.info("处理完成!")

    # 打印统计
    print("\n" + "=" * 50)
    print("处理结果统计")
    print("=" * 50)
    print(f"单个 SKU 数量: {len(single_df)}")
    print(f"组合 SKU 数量: {len(combo_df)}")
    print(f"输出文件:")
    print(f"  - {single_output}")
    print(f"  - {combo_output}")


if __name__ == "__main__":
    main()
