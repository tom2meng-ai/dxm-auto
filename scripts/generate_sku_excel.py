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
import logging
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

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
    COLOR_MAP,
    PRODUCT_NAME_MAP,
    load_card_mapping,
    parse_platform_sku,
    parse_product_spec,
    generate_single_sku,
    generate_combo_sku,
    get_chinese_name,
    get_declare_names,
    validate_excel_columns,
    validate_name_format,
    validate_name2_required,
)

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


def process_orders(input_file: str, date_str: str) -> tuple:
    """
    处理订单文件

    返回: (单个SKU DataFrame, 组合SKU DataFrame, 错误报告 DataFrame)
    """
    logger.info(f"读取输入文件: {input_file}")

    # 读取 Excel
    df = pd.read_excel(input_file)
    total_rows = len(df)
    logger.info(f"输入文件总行数: {total_rows}")

    # 校验必填列
    is_valid, missing_cols, error_msg = validate_excel_columns(df)
    if not is_valid:
        logger.error(error_msg)
        raise ValueError(error_msg)

    # 存储结果
    single_sku_rows = []
    combo_sku_rows = []
    error_rows = []

    # 加载卡片对应表
    card_mapping = load_card_mapping()

    # 统计非定制订单
    df_non_engraved = df[~df["SKU"].str.contains("engraved", case=False, na=False)]
    logger.info(f"非定制订单数: {len(df_non_engraved)}")

    # 将非定制订单记录到错误报告
    for idx, row in df_non_engraved.iterrows():
        order_no = row.get("订单号", "")
        platform_sku = row.get("SKU", "")
        error_rows.append({
            "订单号": order_no,
            "平台SKU": platform_sku,
            "错误原因": "非定制订单（不含engraved）"
        })
        logger.warning(f"非定制订单跳过: {order_no} - {platform_sku}")

    # 过滤 engraved 订单
    df_engraved = df[df["SKU"].str.contains("engraved", case=False, na=False)]
    logger.info(f"定制订单数: {len(df_engraved)}")

    if df_engraved.empty:
        logger.warning("没有找到 engraved 订单")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(error_rows)

    # 处理每一行定制订单
    for idx, row in df_engraved.iterrows():
        order_no = row.get("订单号", "")
        platform_sku = row.get("SKU", "")
        product_spec = row.get("产品规格", "")
        product_image_url = row.get("产品图片网址", "")  # 新增：读取产品图片网址

        # 解析数据（传入已加载的 card_mapping 避免重复读取文件）
        sku_info = parse_platform_sku(platform_sku, card_mapping)
        spec_info = parse_product_spec(product_spec)

        if not sku_info:
            logger.warning(f"无法解析 SKU: {platform_sku}")
            error_rows.append({
                "订单号": order_no,
                "平台SKU": platform_sku,
                "错误原因": "无法解析SKU格式"
            })
            continue

        if not spec_info["name1"]:
            logger.warning(f"缺少 Name1: 订单 {order_no}")
            error_rows.append({
                "订单号": order_no,
                "平台SKU": platform_sku,
                "错误原因": "缺少 Name1（客户姓名）"
            })
            continue

        # 验证 name1 格式（只允许英文字母和数字）
        is_valid_name1, invalid_chars1 = validate_name_format(spec_info["name1"])
        if not is_valid_name1:
            logger.warning(f"名字格式无效: 订单 {order_no} - Name1 含非法字符 {invalid_chars1}")
            error_rows.append({
                "订单号": order_no,
                "平台SKU": platform_sku,
                "错误原因": f"Name1 '{spec_info['name1']}' 包含无效字符: {invalid_chars1}，只允许英文字母和数字"
            })
            continue

        # 验证 name2 格式（如果有值）
        if spec_info["name2"]:
            is_valid_name2, invalid_chars2 = validate_name_format(spec_info["name2"])
            if not is_valid_name2:
                logger.warning(f"名字格式无效: 订单 {order_no} - Name2 含非法字符 {invalid_chars2}")
                error_rows.append({
                    "订单号": order_no,
                    "平台SKU": platform_sku,
                    "错误原因": f"Name2 '{spec_info['name2']}' 包含无效字符: {invalid_chars2}，只允许英文字母和数字"
                })
                continue

        # 验证 name3-name6 格式（如果有值）
        name_valid = True
        for name_key in ["name3", "name4", "name5", "name6"]:
            name_value = spec_info.get(name_key, "")
            if name_value:
                is_valid, invalid_chars = validate_name_format(name_value)
                if not is_valid:
                    logger.warning(f"名字格式无效: 订单 {order_no} - {name_key.upper()} 含非法字符 {invalid_chars}")
                    error_rows.append({
                        "订单号": order_no,
                        "平台SKU": platform_sku,
                        "错误原因": f"{name_key.upper()} '{name_value}' 包含无效字符: {invalid_chars}，只允许英文字母和数字"
                    })
                    name_valid = False
                    break
        if not name_valid:
            continue

        # 验证双名字格式时 Name2 不能为空
        is_name2_valid, name2_error = validate_name2_required(spec_info)
        if not is_name2_valid:
            logger.warning(f"Name2为空: 订单 {order_no} 使用双名字格式但缺少 Name2")
            error_rows.append({
                "订单号": order_no,
                "平台SKU": platform_sku,
                "错误原因": name2_error
            })
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
            "图片URL\n（必须以http://或https：//开头）": product_image_url,  # 使用产品图片网址
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
            "识别码": f"{order_no}-GROUP",  # 组合SKU识别码加上-GROUP后缀
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
    error_df = pd.DataFrame(error_rows)

    # 数量核对
    success_count = len(single_df)
    error_count = len(error_df)
    total_check = success_count + error_count

    logger.info("=" * 50)
    logger.info("数量核对")
    logger.info("=" * 50)
    logger.info(f"输入文件总行数: {total_rows}")
    logger.info(f"成功导出订单数: {success_count}")
    logger.info(f"错误/跳过订单数: {error_count}")
    logger.info(f"处理总数: {total_check}")

    if total_check == total_rows:
        logger.info("✅ 数量核对通过！所有订单都已处理")
    else:
        logger.error(f"❌ 数量核对失败！遗漏 {total_rows - total_check} 个订单")
        logger.error("请检查是否有订单被意外跳过")

    return single_df, combo_df, error_df


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
    single_df, combo_df, error_df = process_orders(args.input_file, args.date)

    # 输出文件
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    single_output = output_dir / f"output_单个SKU_{timestamp}.xlsx"
    combo_output = output_dir / f"output_组合SKU_{timestamp}.xlsx"
    error_output = output_dir / f"output_错误报告_{timestamp}.xlsx"

    # 保存单个 SKU Excel
    if not single_df.empty:
        single_df.to_excel(single_output, index=False)
        logger.info(f"单个 SKU 文件已生成: {single_output} ({len(single_df)} 条)")
    else:
        logger.warning("没有生成任何单个 SKU 数据")

    # 保存组合 SKU Excel
    if not combo_df.empty:
        combo_df.to_excel(combo_output, index=False)
        logger.info(f"组合 SKU 文件已生成: {combo_output} ({len(combo_df)} 条)")
    else:
        logger.warning("没有生成任何组合 SKU 数据")

    # 保存错误报告 Excel
    if not error_df.empty:
        error_df.to_excel(error_output, index=False)
        logger.info(f"错误报告文件已生成: {error_output} ({len(error_df)} 条)")
    else:
        logger.info("没有错误订单")

    logger.info("处理完成!")

    # 打印统计
    print("\n" + "=" * 50)
    print("处理结果统计")
    print("=" * 50)
    print(f"✅ 成功导出订单数: {len(single_df)}")
    print(f"❌ 错误/跳过订单数: {len(error_df)}")
    print(f"📊 组合 SKU 行数: {len(combo_df)}")
    print(f"\n输出文件:")
    if not single_df.empty:
        print(f"  - {single_output}")
    if not combo_df.empty:
        print(f"  - {combo_output}")
    if not error_df.empty:
        print(f"  - {error_output}")
    print("=" * 50)


if __name__ == "__main__":
    main()
