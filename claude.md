# Claude 项目指令文档

> 本文档用于定义项目上下文，帮助 Claude 在后续对话中更好地理解和协助开发。

---

## 项目概述

**项目名称**：店小秘 SKU 自动配对系统  
**目标用户**：跨境电商卖家（定制首饰）  
**核心功能**：自动化处理 Shopify 定制订单在店小秘 ERP 中的 SKU 创建与配对

---

## 技术栈

| 技术 | 用途 | 版本要求 |
|------|------|----------|
| Python | 主开发语言 | 3.9+ |
| Playwright | 浏览器自动化 | 最新版 |
| pandas | Excel 数据处理 | 最新版 |
| openpyxl | Excel 文件读写 | 最新版 |

---

## 运行环境

- **主要环境**：Windows
- **测试环境**：MacBook (macOS)
- **目标网站**：https://www.dianxiaomi.com （店小秘 ERP）

---

## 核心业务逻辑

### 1. 平台 SKU 结构（Shopify）

```
{产品编号}-{颜色}-{定制类型}-{卡片代码}-{盒子类型}

示例：B09-B-Engraved-MAN10-whitebox
      J20-G-engraved-D17-whitebox
      J02-S-engraved-M58-whitebox
```

**字段解析：**
- `产品编号`：B09, J20, J02 等
- `颜色`：B=Black, G=Gold, S=Silver
- `定制类型`：Engraved=定制（需处理），其他=非定制（跳过）
- `卡片代码`：MAN10, D17, M58, DAD1 等（需要查对应表）
- `盒子类型`：whitebox=白盒（默认），ledbox=红盒（需加 SKU）

### 2. 店小秘 SKU 生成规则

```
格式：Michael-{产品编号}-{日期MMDD}-{Name1}+{Name2}

示例：
输入：
  - 平台SKU: J20-G-engraved-D17-whitebox
  - Name 1: Xaviar
  - Name 2: Suzi
  - 日期: 01月21日

输出：
  - 新SKU: Michael-J20-0121-Xaviar+Suzi
```

### 3. 额外 SKU 规则

| 条件 | 操作 |
|------|------|
| 有卡片代码 | 添加对应的卡片 SKU（查对应表）|
| ledbox | 添加 `Michael-RED BOX` |
| whitebox | 不需要额外操作 |

### 4. 卡片对应表

```json
{
  "MAN1": "待确认",
  "MAN10": "待确认",
  "D17": "待确认",
  "M58": "待确认",
  "DAD1": "待确认"
}
```

> ⚠️ 用户稍后提供完整对应表，届时更新此处

---

## 店小秘操作路径

### 订单配对流程

```
1. 访问：订单 → 待审核
   URL: https://www.dianxiaomi.com/web/order/paid?go=m100

2. 找到未配对订单（"未配对SKU" 筛选）

3. 点击订单的"详情"按钮

4. 在弹窗中点击"配对商品SKU"链接

5. 在商品搜索页面：
   - 搜索类型：商品SKU
   - 输入搜索内容
   - 点击"搜索"
   - 点击对应结果的"选择"

6. 配对完成
```

### 批量建库路径

```
路径：仓库 → 商品管理 → 导入
```

### 页面元素定位（供 Playwright 使用）

| 元素 | 描述 | 定位方式（待确认）|
|------|------|------------------|
| 订单详情按钮 | "详情"链接 | 文本匹配 |
| 配对SKU按钮 | "配对商品SKU"链接 | 文本匹配 |
| 搜索输入框 | 商品搜索页面的输入框 | 待确认 |
| 搜索按钮 | "搜索"按钮 | 文本匹配 |
| 选择按钮 | 搜索结果的"选择"链接 | 文本匹配 |

---

## 文件结构

```
项目目录/
├── README.md                 # 使用说明
├── requirements.txt          # Python 依赖
├── config/
│   ├── config.json          # 主配置文件
│   └── card_mapping.json    # 卡片对应表
├── scripts/
│   ├── generate_sku_excel.py    # Excel 生成脚本
│   └── auto_pair_sku.py         # 自动配对脚本
├── data/
│   ├── input/               # 输入文件（订单导出）
│   └── output/              # 输出文件（建库Excel）
└── logs/                    # 运行日志
```

---

## 代码规范

### Python 风格

```python
# 1. 使用类型注解
def parse_platform_sku(sku: str) -> dict:
    pass

# 2. 函数命名：小写下划线
def generate_new_sku():
    pass

# 3. 类命名：大驼峰
class SkuPairAutomation:
    pass

# 4. 常量：大写下划线
DEFAULT_STORE_NAME = "Michael"
RED_BOX_SKU = "Michael-RED BOX"

# 5. 错误处理：明确捕获
try:
    element.click()
except TimeoutError:
    logger.error("元素点击超时")
```

### Playwright 规范

```python
# 1. 使用显式等待
page.wait_for_selector(".order-detail")

# 2. 优先使用文本定位
page.click("text=配对商品SKU")

# 3. 添加适当延迟（避免过快操作）
page.wait_for_timeout(500)

# 4. 截图保存关键步骤（调试用）
page.screenshot(path="debug/step1.png")
```

---

## 配置文件格式

### config.json

```json
{
  "store_name": "Michael",
  "red_box_sku": "Michael-RED BOX",
  "dianxiaomi": {
    "base_url": "https://www.dianxiaomi.com",
    "order_page": "/web/order/paid?go=m100"
  },
  "browser": {
    "headless": false,
    "slow_mo": 100
  }
}
```

### card_mapping.json

```json
{
  "MAN1": "Michael-MAN CARD1",
  "MAN10": "Michael-MAN CARD10",
  "D17": "Michael-D CARD17",
  "M58": "Michael-M CARD58",
  "DAD1": "Michael-DAD CARD1"
}
```

> ⚠️ 以上为示例格式，具体值待用户确认

---

## 开发注意事项

### 必须遵守

1. **不存储用户密码** - 使用浏览器保存的登录状态
2. **添加错误处理** - 网络超时、元素找不到等情况
3. **记录日志** - 每次操作记录成功/失败
4. **支持中断续传** - 记录已处理订单，支持断点继续

### 优化建议

1. **批量处理** - 尽量减少页面跳转
2. **并发控制** - 不要过快操作，避免被限制
3. **失败重试** - 单个失败不影响整体流程

---

## 测试检查清单

- [ ] Excel 解析正确提取 Name1、Name2
- [ ] SKU 生成规则正确
- [ ] 卡片对应表匹配正确
- [ ] 红盒/白盒判断正确
- [ ] Playwright 能正确登录店小秘
- [ ] 能正确点击"详情"按钮
- [ ] 能正确点击"配对商品SKU"
- [ ] 能正确搜索 SKU
- [ ] 能正确点击"选择"完成配对
- [ ] 错误处理正常工作
- [ ] 日志记录完整

---

## 待办事项

- [ ] 用户提供完整卡片对应表
- [ ] 确认所有产品编号列表
- [ ] 确认所有颜色代码列表
- [ ] 测试 Playwright 在店小秘的稳定性
- [ ] 确定是否需要定时自动运行

---

## 联系上下文

当用户提到以下关键词时，参考本文档：

- 店小秘、电小秘、dianxiaomi
- SKU配对、SKU匹配
- 定制订单、Engraved
- 卡片、红盒、白盒、ledbox
- Name1、Name2、客户名字
- Shopify、首饰、项链、手链

---

**文档版本**：v1.0  
**最后更新**：2026-01-21