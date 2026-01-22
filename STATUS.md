# STATUS.md

## 目的
- 修复店小秘自动配对流程，确保能自动登录、筛选未配对订单、解析 SKU、提取 Name1/Name2、搜索并选择 SKU。
- 优化 SKU 生成器的唯一性保证机制，确保识别码和 SKU 不重复。

---

## 当前改动与结果

### 自动配对功能 (scripts/auto_pair_sku.py) - 2026-01-22 更新

#### ✅ 已修复的问题

1. **"详情"按钮点击问题** - 已修复
   - 问题：之前代码会遍历点击所有按钮，导致误点"审核"按钮
   - 解决：使用 `page.get_by_role("link", name="详情")` 精确定位
   - 来源：通过 `npx playwright codegen` 录制获取正确选择器

2. **"配对商品SKU"链接点击问题** - 已修复
   - 问题：`_dismiss_overlays()` 方法关闭了详情弹窗，导致找不到链接
   - 解决：在 `click_pair_sku_button()` 和 `process_current_order_in_detail()` 中移除了 `_dismiss_overlays()` 调用
   - 使用 `page.get_by_role("link", name="配对商品SKU")` 精确定位

3. **自动点击"审核"按钮问题** - 已修复
   - 问题：代码在多处自动调用 `click_review_button()`，导致订单被错误审核
   - 解决：移除所有自动审核调用，让用户手动审核

4. **代码简化**
   - 删除了 `open_order_detail()` 中复杂的嵌套函数和多重尝试逻辑
   - 删除了 `click_pair_sku_button()` 中冗余的多种定位方法
   - 代码更清晰，维护更容易

#### ✅ 测试验证通过的流程

| 步骤 | 状态 | 说明 |
|------|------|------|
| 1. 筛选未配对订单 | ✅ 成功 | 点击"未配对SKU"筛选 |
| 2. 点击"详情"按钮 | ✅ 成功 | 不再误点"审核" |
| 3. 检测未配对状态 | ✅ 成功 | 识别"配对商品SKU"链接 |
| 4. 点击"配对商品SKU" | ✅ 成功 | 弹出搜索弹窗 |
| 5. 输入SKU并搜索 | ✅ 成功 | 找到输入框和搜索按钮 |
| 6. 点击"选择"完成配对 | ⚠️ 待验证 | 需要SKU已存在于商品库 |

#### ⚠️ 当前卡点

**SKU 不存在问题**：搜索 `Michael-J20-0122-Harminder+Harpreet` 时未找到匹配结果
- 原因：该 SKU 尚未导入店小秘商品库
- 解决：需要先用 Web 界面生成 SKU 并导入，再运行自动配对

---

### SKU 生成器 (web_app.py) ✅ 已完成 (2026-01-22)

- **识别码格式优化**：`订单后5位-产品编号-完整Name`
- **SKU 唯一性保证**：自动检测重复，冲突时添加订单号后缀
- **错误报告增强**：详细的错误类型和建议操作
- **提交记录**：commit `dca96fe` 已推送到 GitHub

---

## 待办（下一步）

### 优先级 1：优化 engraved 订单筛选
- [ ] 在列表页就过滤 engraved 订单，避免打开非定制订单的详情
- [ ] 只处理 SKU 包含 "engraved" 的订单

### 优先级 2：验证完整配对流程
- [ ] 使用一个已导入 SKU 的订单测试完整流程
- [ ] 确认"选择"按钮点击成功

### 优先级 3：错误处理优化
- [ ] SKU 不存在时的友好提示
- [ ] 配对失败后的重试机制

---

## 关键文件

| 文件 | 说明 |
|------|------|
| scripts/auto_pair_sku.py | 自动配对主脚本 |
| web_app.py | SKU 生成器 Web 界面 |
| config/config.json | 配置文件 |
| config/card_mapping.json | 卡片代码映射表 |

---

## 运行命令

```bash
# 保存登录态
python3 scripts/auto_pair_sku.py --save-auth

# 小批量测试（1个订单）
python3 scripts/auto_pair_sku.py --max-orders 1

# 批量处理（10个订单）
python3 scripts/auto_pair_sku.py --max-orders 10

# 启动 SKU 生成器 Web 界面
python3 web_app.py
```

---

## 调试文件位置

- `logs/debug/before_filter.png` - 筛选前截图
- `logs/debug/after_filter.png` - 筛选后截图
- `logs/debug/pair_button_not_found.png` - 配对按钮未找到截图
- `logs/debug/pair_search_start.png` - 搜索开始截图
- `logs/auto_pair.log` - 运行日志

---

**最后更新**：2026-01-22 21:30
