# 阶段 13：移动端布局紧凑度与走势线修复

状态：`COMPLETE`

完成日期：2026-07-31

## 目标

在阶段 10–12 的响应式基础上，用程序化布局度量（CDP 几何检查）找出移动端页面过高的区块，压缩无信息量的纵向空间，并修复紧凑走势线高度被全局规则覆盖的 CSS 优先级 bug。

## 范围

- 修复 `.compact-sparkline`（24px）被同优先级且更靠后的 `.sparkline`（52px）覆盖的问题，行情带走势线实际高度与设计意图一致。
- 移动端自助选股筛选表单由单列改为两列网格，checkbox 与按钮跨列。
- 移动端页面头部（markets / recommendations）的 freshness 卡片改为横向基线排列。
- 移动端荐股行与首页候选卡片压缩内边距、网格间距，并移除单列布局下无意义的等高 min-height。
- 移动端登录页压缩品牌区、标题、表单垂直间距，使其适配 844px 视口不滚动。
- 静态资源版本号 `?v=12 → ?v=13`。

## 非目标

- 不改变桌面端布局、信息层级、数据源或任何服务端逻辑。
- 不压缩触控目标尺寸（44px 最小高度保持不变）。

## 设计决策

- 以真实浏览器 CDP 测量替代目测：对六个业务页在 1440/390 两个宽度测量页面高度、区块几何、卡片高度一致性与横向溢出，用数据定位问题并验收。
- 移动端等高 min-height（候选理由 43px、关注理由 39px）只在多列网格中有意义，单列时移除。
- 登录页垂直节奏优先于留白：844px 视口下首屏完整性比大间距更重要。

## 数据/API 变化

无。仅静态样式与两个模板中的资源版本号。

## 验收标准

- 行情带走势线渲染高度为 24px（修复前实测 52px）。
- 390px 下自助选股筛选表单高度从 462px 降至 300px 以内；页面总高从 1781px 降至 1620px 以内。
- 390px 下 markets / recommendations 页面头部从 224px 降至 180px 以内。
- 390px 下登录页 `scrollHeight ≤ 844`，无垂直滚动。
- 1440px 与 390px 均无横向溢出；卡片高度保持一致。
- Ruff、全量单测、编译检查、`git diff --check` 全部通过。

## 验证命令

```bash
ruff check .
python -m unittest discover -s tests -v
python -m compileall -q .
git diff --check
```

## 实施记录

- 修复 `static/app.css` 中 `.sparkline.compact-sparkline` 优先级：将覆盖规则放在 `.sparkline` 之后并提升选择器特异性，行情带走势线从 52px 恢复为 24px。
- ≤560px 下 `.filter-form` 改为 `1fr 1fr` 两列，`filter-check` 与主按钮跨全列；表单高度 462px → 299px，整页 1781px → 1617px。
- ≤800px 下 `.hero .freshness` 改为 flex 横排基线布局；markets / recommendations 移动端头部 224px → 176px。
- ≤560px 下荐股行内边距 14px → 12px/13px、指标网格间距收窄、失效位行距收窄，行高 401px → 389px；≤800px 下移除候选/关注卡片理由与摘要的固定 min-height，候选卡 214px → 208px，关注卡 189px → 168px。
- ≤560px 下压缩登录页品牌下距、标题外边距、高亮区上距、表单外边距与卡片内边距；390px 下页面高度 896px → 844px，正好适配视口。
- 模板静态资源版本号更新为 `?v=13`，同步更新 `tests/test_web_app.py` 中对应断言。
- 验证结果：`venv/bin/ruff check .` 通过；全量单测 79 项通过；`compileall` 与 `git diff --check` 通过；CDP 复测六个页面在 1440/390 下均无横向溢出，以上各指标均达到验收值。
