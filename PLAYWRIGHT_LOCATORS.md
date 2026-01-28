# 店小秘页面元素定位文档

> **重要**: 此文档记录了所有店小秘页面的 Playwright 定位方式。
> 修改代码时请参考此文档，不要随意更改这些定位方式！

---

## 订单列表页面

### 筛选和搜索

| 功能 | 定位方式 | 说明 |
|------|----------|------|
| 未配对SKU筛选 | `locator("text=/未配对SKU\\(\\d+\\)/")` | 正则匹配带数字的文本 |
| 未配对SKU筛选(备用) | `locator("text=未配对SKU")` | 纯文本匹配 |

### 订单操作

| 功能 | 定位方式 | 说明 |
|------|----------|------|
| 详情按钮 | `get_by_role("link", name="详情")` | **codegen录制** |
| 审核按钮 | `locator("button:has-text('审核')")` | |
| 下一个按钮 | `get_by_role("button", name="下一个")` | |
| 订单行定位 | `locator(f"tr[rowid='{row_id}']")` | 通过行ID定位 |
| 订单行定位(备用) | `locator("tr", has=locator(f"text={order_no}"))` | 通过订单号定位 |

---

## 订单详情弹窗

### 配对操作 ⚠️ 核心定位

| 功能 | 定位方式 | 说明 |
|------|----------|------|
| **配对商品SKU链接** | `get_by_role("link", name="配对商品SKU")` | **codegen录制，勿修改！** |
| 配对商品SKU(备用) | `locator("text=配对商品SKU")` | 文本匹配备用 |
| 更换按钮 | `locator("text=更换")` | 已配对订单显示 |
| 解除按钮 | `locator("text=解除")` | 已配对订单显示 |

### 弹窗检测

| 功能 | 定位方式 | 说明 |
|------|----------|------|
| 包裹标识 | `locator("text=包裹")` | 检测详情弹窗是否打开 |
| 弹窗容器 | `locator(".ant-modal, .modal, dialog")` | |
| 弹窗关闭按钮 | `locator(".ant-modal-close")` | |

### 产品区块

| 功能 | 定位方式 | 说明 |
|------|----------|------|
| 产品区块(class) | `locator(f".order-sku:has-text('{product_sku}')")` | |
| 产品区块(tr) | `locator(f"tr:has-text('{product_sku}')")` | 备用定位 |
| 产品数量 | `locator(".order-sku__meta > .order-sku__quantity")` | |

---

## 配对搜索弹窗

### 搜索和选择

| 功能 | 定位方式 | 说明 |
|------|----------|------|
| 搜索输入框 | `locator("#newSearchWareHoseProductsValue")` | |
| 搜索按钮 | `get_by_role("button", name="搜索")` | |
| 选择按钮 | `get_by_role("button", name="选择")` | |
| 确定按钮 | `get_by_role("button", name="确定")` | |
| 确定按钮(备用) | `locator("button:has-text('确定')")` | |
| 确定选择按钮 | `get_by_role("button", name="确定选择")` | |

---

## 追加商品操作

| 功能 | 定位方式 | 说明 |
|------|----------|------|
| 编辑/追加链接 | `get_by_role("link", name="编辑/追加")` | |
| 追加商品链接 | `get_by_role("link", name="追加商品")` | |
| 追加额外商品按钮 | `get_by_text("追加额外商品")` | |
| 填写数量输入框 | `get_by_placeholder("填写数量")` | |
| 移除链接 | `get_by_role("link", name="移除")` | |
| 保存链接 | `get_by_role("link", name="保存")` | |

---

## 其他弹窗

| 功能 | 定位方式 | 说明 |
|------|----------|------|
| 同步订单弹窗 | `locator(".ant-modal-root:has-text('同步订单')")` | |
| 产品动态弹窗 | `locator(".ant-modal-root:has-text('产品动态')")` | |
| 关闭按钮 | `locator("button:has-text('关闭')")` | |
| 遮罩层 | `locator(".ant-modal-wrap, .ant-modal-mask")` | |

---

## 登录状态检测

| 功能 | 定位方式 | 说明 |
|------|----------|------|
| 已登录标识 | `locator(".layout-main, .main-content, .user-info, .header-user")` | |

---

## Codegen 录制的关键定位

以下是通过 Playwright Codegen 录制的定位方式，**请勿修改**：

```javascript
// 点击详情按钮
await page.getByRole('link', { name: '详情' }).click();

// 点击配对商品SKU链接
await page.getByRole('link', { name: '配对商品SKU' }).click();

// 点击搜索按钮
await page.getByRole('button', { name: '搜索' }).click();

// 点击选择按钮
await page.getByRole('button', { name: '选择' }).click();

// 点击确定按钮
await page.getByRole('button', { name: '确定' }).click();
```

---

## 注意事项

1. **不要随意修改定位方式**：这些定位方式是经过测试验证的
2. **优先使用 `get_by_role`**：这是 Playwright 推荐的定位方式
3. **备用方案**：如果主定位失败，代码中有备用的文本匹配方式
4. **店小秘页面结构稳定**：通常不需要修改这些定位

---

**文档版本**: v1.0
**最后更新**: 2026-01-28
