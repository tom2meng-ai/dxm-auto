STATUS.md

目的
- 修复店小秘自动配对流程，确保能自动登录、筛选未配对订单、解析 SKU、提取 Name1/Name2、搜索并选择 SKU。
- 优化 SKU 生成器的唯一性保证机制，确保识别码和 SKU 不重复。

当前改动与结果

## 自动配对功能 (scripts/auto_pair_sku.py)
- 统一登录域名为 https://www.dianxiaomi.com，并保存登录态可识别 home.htm。
- 列表页/详情页 SKU 解析已改进：详情页从 .order-sku__meta 抽取并优先包含 engraved 的平台 SKU。
- Name1/Name2 提取增强：支持输入框、文本标签、标签跨行等多种模式。
- 搜索配对弹窗逻辑增强：等待结果表格、尝试匹配 SKU 行并点"选择"，并保存调试截图/HTML。
- 已增加日志：判定平台SKU及 custom_type。
- 最近一次自动匹配失败点：点击配对按钮失败，原因是"产品动态"弹窗遮挡（detail_opened.png 显示）。
- 筛选未配对 SKU 仍会超时（filter_timeout）。

## SKU 生成器优化 (web_app.py) ✅ 已完成 (2026-01-22)
- **识别码格式优化**：从 "订单号/Name" 改为 "订单后5位-产品编号-完整Name"
  - 旧格式：`5261219-59178/Jon+Lauren` (25+ 字符)
  - 新格式：`59178-J20-Jonathan` (14-23 字符)
  - 保留完整客户名字，提高可读性和识别度

- **SKU 唯一性保证**：添加自动重复检测机制
  - 函数重命名：`generate_single_sku()` → `generate_single_sku_unique()`
  - 新增 `sku_counter` 字典进行重复检测
  - 重复 SKU 自动添加订单号后缀（如：`Michael-J20-0122-Jon+Lauren-59999`）
  - 确保 100% 唯一性

- **识别码冲突检测**：防止识别码重复
  - 新增 `identifier_set` 集合进行去重检测
  - 检测同订单、同产品、同名字的极端冲突情况
  - 冲突时自动跳过并记录到错误报告

- **错误报告增强**：
  - 新增错误类型："识别码重复冲突"
  - 详细说明冲突原因和建议操作
  - 帮助用户快速定位问题订单

- **提交记录**：commit `dca96fe` 已推送到 GitHub

待办（建议下一步）
1) 修复“点击配对按钮失败”（当前被产品动态弹窗遮挡）：
   - 打开详情后先关闭弹窗；或在点击配对按钮前强制移除遮罩层。
   - 定位详情页中的“配对/商品配对/配对SKU”按钮，避免点到顶部导航“商品配对”。
2) 处理“筛选未配对 SKU 超时”：
   - 增加更稳的等待与重试，或在超时后继续尝试点击过滤条件。
3) 搜索 SKU 仍可能超时：
   - 根据 logs/debug/pair_search_timeout.html 精准定位搜索输入框与按钮。
   - 当前弹窗中 “选择” 按钮存在，但搜索输入框可能不在 HTML（动态/Shadow/iframe）。

相关日志与调试文件
- logs/debug/after_filter.html / before_filter.html
- logs/debug/pair_search_timeout.html
- logs/debug/filter_timeout.html
- logs/debug/detail_opened.html / detail_opened.png

关键文件
- scripts/auto_pair_sku.py
- config/config.json

运行命令
- 保存登录态：python3 scripts/auto_pair_sku.py --save-auth
- 小批量测试：python3 scripts/auto_pair_sku.py --max-orders 1
