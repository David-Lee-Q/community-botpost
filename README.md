# 社区内容自运营智能体

面向卡奥斯开源社区（openlab.cosmoplat.com）的自动化内容运营系统：bot 按计划定时发布原创科技文章，发布台账展示页实时呈现发文统计与明细，评价引擎沉淀质量闭环，飞书定时推送日报/周报。

## 功能特性

- **定时自动发文**：runner 每 30 秒轮询发布计划，到点自动调用平台发布接口，每天 6 点可生成新一批计划
- **防重复发布**：按标题精确查重，同名文章不重复推送
- **详情 URL 记录**：发布后自动抓取文章详情页真实地址，写入计划文件与当日清单
- **发布台账**：可视化每日/每周/每月/历史累计发文量、分类分布与趋势，支持搜索、筛选、分页
- **数据自动刷新**：发布完成后与每 6 小时定时刷新台账数据（浏览/互动实时增长）
- **评价闭环**：全量已发布文章自动评价，每周一综合评分并沉淀改进任务
- **飞书日报/周报**：每日 18:00、每周一 10:00 推送总结卡片到飞书
- **图表分析**：发布量、分类分布、评价分数、浏览/互动趋势（按日/周/月，同比+环比）

## 目录结构

```
├── bot/                      发文 bot
│   ├── publish.py            发布与查重（含分类 ID 映射、token 刷新）
│   ├── runner.py             定时守护主循环（发布/评分/刷新/报告触发）
│   ├── report.py             每日/每周总结生成与飞书推送
│   ├── fetch_detail_urls.py  发布后从「我的文章」页抓取详情 URL
│   ├── plan.json             发布计划与状态（含 detail_url、cover）
│   ├── HEARTBEAT.md          每日发布清单
│   ├── SCHEDULE.md           全部定时任务说明
│   ├── covers/<主题>/        封面图按主题分类（ai/、smart-manufacturing/ 等）
│   ├── credentials.json      登录凭据（权限 600，不入库）
│   └── token.txt             登录令牌（JWT，约 7 天有效，权限 600，不入库）
├── bot-articles/             文章正文（markdown）
├── ledger/                   发布台账展示页
│   ├── index.html            前端页面（Chart.js，8 个图表面板）
│   ├── fetch_articles.py     数据拉取（自动触发自动评价）
│   ├── auto_review.py        已发布文章自动评价
│   ├── review_engine.py      每周综合评分 + 闭环文件生成
│   └── data/                 文章/评价/评分数据（自动生成）
├── .cosmocode/
│   ├── MEMORY.md             项目指令记忆
│   ├── quality_tasks.md      发文质量改进任务清单
│   └── docs/                 审计、测试、评价标准与运营文档
└── .gitignore
```

## 快速开始

### 1. 安装依赖

```bash
pip install requests playwright
python -m playwright install chromium
```

### 2. 配置登录

平台登录凭据通过环境变量提供，不写入源码：

```bash
export OPENLAB_ACCOUNT="你的账号"
export OPENLAB_PASSWORD="你的密码"
```

首次运行时 bot 会自动打开平台登录页获取令牌，并写入 `bot/token.txt`（JWT 约 7 天有效，失效后自动重登）。

### 3. 启动发文守护

```bash
python bot/runner.py
```

启动后按 `bot/SCHEDULE.md` 中的定时任务表自动执行发布、评分、数据刷新与飞书报告推送。

### 4. 部署台账展示页

```bash
cd ledger && python3 -m http.server 8090 --bind 0.0.0.0
```

浏览器访问 `http://localhost:8090/` 即打开台账首页。需要手动刷新数据时执行：

```bash
python ledger/fetch_articles.py
```

## 定时任务

| 时间 | 任务 | 说明 |
|------|------|------|
| 每 30 秒 | 发布计划轮询 | 到点调用 `publish.py` 发布，随后抓详情 URL、更新 HEARTBEAT、刷新台账 |
| 每日 06:00 | 生成新一批计划 | 扩展点，可接入计划生成逻辑 |
| 每周一 06:00 | 综合评分 | `review_engine.py` 计算上一完整周综合分并沉淀改进任务 |
| 每日 0/6/12/18 点 | 刷新台账数据 | `fetch_articles.py`（含自动评价），保证浏览/互动最新 |
| 每日 18:00 | 飞书每日总结 | `report.py daily`，蓝色卡片 |
| 每周一 10:00 | 飞书每周总结 | `report.py weekly`，绿色卡片 |

完整说明见 `bot/SCHEDULE.md`。

## 飞书报告推送

- Webhook：`bot/report.py` 中的 `WEBHOOK`（机器人 webhook 地址）
- 每日总结：今日发布/浏览量/互动、文章表现表格（标题超链接直达详情页）、综合评分
- 每周总结：本周发布/浏览量/互动、综合评分（按分档着色）、短板维度、改进建议、下周待发
- 手动预览（不推送）：

```bash
python bot/report.py daily --preview
python bot/report.py weekly --preview
```

## 发布计划配置

编辑 `bot/plan.json`，按以下格式新增文章条目：

```json
{
  "time": "2026-08-09 08:00",
  "title": "文章标题",
  "category": "人工智能",
  "file": "/workspace/bot-articles/article-5.md",
  "summary": "120字左右摘要",
  "images": [],
  "cover": "/workspace/bot/covers/<主题>/cover5.jpg"
}
```

分类可选值见 `bot/publish.py` 的 `CATEGORY_IDS`。

## 文章与配图规范

- 封面图：800×400，jpg/png，≤1MB；保存到 `bot/covers/<主题关键词>/` 按主题分类，避免多篇混用
- 正文：markdown，≥1000 字，每段≥200 字，至少 2 张配图
- 正文配图来源（须免费可商用，直接引用图片链接）：magnific.com、Pexels、Unsplash、Pixabay
- 标题禁用冒号；正文不得重复标题或二级标题文字；需提供约 120 字摘要
- 评价标准见 `.cosmocode/docs/社区文章质量评价标准.md`（100 分制 6 维度，8 项一票否决）

## 相关文档

- 运营工作流（skill）：`.cosmocode/docs/内容运营工作流.md`
- 定时任务：`bot/SCHEDULE.md`
- 评价标准：`.cosmocode/docs/社区文章质量评价标准.md`
- 评价结果：`.cosmocode/docs/文章质量评价结果.md`
- 质量改进记录：`.cosmocode/docs/质量改进记录.md`
- 安全审计结论：`.cosmocode/docs/安全审计结论.md`
- 测试报告：`.cosmocode/docs/测试报告.md`
- 目标用户全覆盖证明：`.cosmocode/docs/目标用户全覆盖证明.md`
