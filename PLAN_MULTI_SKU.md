# 多SKU订单支持功能计划

## 分支信息
- **分支名**: `feature/multi-sku`
- **基于**: `main` (commit: 76bcb13)

---

## 问题描述

当前 `auto_pair_sku.py` 脚本**只能处理单个SKU的订单**。

如果一个订单包含多个 engraved 产品，例如：
- 产品1: `B09-B-Engraved-MAN10-LEDx1` (Name1: John, Name2: Mary)
- 产品2: `J20-G-engraved-D17-whitebox` (Name1: Tom, Name2: Lisa)

**当前行为**：只配对第一个产品，第二个产品被忽略。

**期望行为**：依次配对所有 engraved 产品。

---

## 需要了解的信息

在开发前，需要确认以下问题：

### 1. 店小秘详情页结构
- [ ] 多个产品在详情页如何展示？（独立区块？列表？）
- [ ] 每个产品的"配对商品SKU"链接位置？
- [ ] Name1/Name2 如何与每个产品对应？

### 2. 配对流程
- [ ] 每个产品是否需要单独点击"配对商品SKU"？
- [ ] 配对完一个后，页面状态如何变化？

---

## 实现方案

### 阶段1：分析页面结构

**任务**：截图并分析多SKU订单的详情页结构

```python
# 保存多SKU订单的调试信息
self.save_debug_info("multi_sku_order")
```

### 阶段2：修改SKU提取函数

**文件**: `scripts/auto_pair_sku.py`

**修改函数**: `_extract_platform_sku_from_detail()`

```python
# 当前：返回单个SKU
def _extract_platform_sku_from_detail(self) -> str:

# 修改为：返回SKU列表
def _extract_all_platform_skus_from_detail(self) -> list[dict]:
    """提取详情页中所有产品的SKU和名称

    Returns:
        [
            {"sku": "B09-B-Engraved-MAN10-LEDx1", "name1": "John", "name2": "Mary"},
            {"sku": "J20-G-engraved-D17-whitebox", "name1": "Tom", "name2": "Lisa"}
        ]
    """
```

### 阶段3：修改名称提取函数

**修改函数**: `_extract_name_from_detail()`

```python
# 当前：只提取第一个Name1/Name2
def _extract_name_from_detail(self, field_name: str) -> str:

# 修改为：按产品索引提取
def _extract_names_for_product(self, product_index: int) -> tuple[str, str]:
    """提取指定产品的Name1和Name2"""
```

### 阶段4：修改主处理函数

**修改函数**: `process_current_order_in_detail()`

```python
def process_current_order_in_detail(self, date_str: str) -> bool:
    # 1. 提取所有产品信息
    products = self._extract_all_platform_skus_from_detail()

    # 2. 过滤出 engraved 产品
    engraved_products = [p for p in products if "engraved" in p["sku"].lower()]

    # 3. 依次配对每个产品
    success_count = 0
    for i, product in enumerate(engraved_products):
        logger.info(f"配对产品 {i+1}/{len(engraved_products)}: {product['sku']}")

        # 点击该产品的"配对商品SKU"链接
        if self._click_pair_button_for_product(i):
            # 生成并配对SKU
            if self._pair_single_product(product, date_str):
                success_count += 1

    return success_count == len(engraved_products)
```

### 阶段5：测试验证

1. 找一个包含2-3个engraved产品的订单
2. 运行脚本测试
3. 验证所有产品都正确配对

---

## 文件修改清单

| 文件 | 修改内容 |
|------|----------|
| `scripts/auto_pair_sku.py` | 主要修改 |
| - `_extract_platform_sku_from_detail()` | 改为返回列表 |
| - `_extract_name_from_detail()` | 支持按索引提取 |
| - `process_current_order_in_detail()` | 循环处理多个产品 |
| - `click_pair_sku_button()` | 支持指定产品索引 |

---

## 开发步骤

1. [ ] 截图分析多SKU订单的页面结构
2. [ ] 实现 `_extract_all_platform_skus_from_detail()`
3. [ ] 实现 `_extract_names_for_product()`
4. [ ] 修改 `click_pair_sku_button()` 支持多产品
5. [ ] 修改 `process_current_order_in_detail()` 循环处理
6. [ ] 测试单SKU订单（确保不破坏现有功能）
7. [ ] 测试多SKU订单
8. [ ] 合并到 main 分支

---

## 风险评估

- **中等风险**：需要修改多个核心函数
- **向后兼容**：单SKU订单仍需正常工作
- **回滚方案**：保留在独立分支，不影响 main

---

## 启动开发

清除 context 后，执行以下命令开始：

```bash
# 切换到功能分支
git checkout feature/multi-sku

# 查看计划
cat PLAN_MULTI_SKU.md

# 告诉 Claude：
# "按照 PLAN_MULTI_SKU.md 的计划，帮我实现多SKU订单支持功能"
```

---

**创建日期**: 2026-01-23
**状态**: 待开发
