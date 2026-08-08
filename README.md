# 社区自动发文与发布台账

面向卡奥斯开源社区（openlab.cosmoplat.com）的自动化内容运营系统：bot 按计划定时发布原创科技文章，发布台账展示页实时呈现发文统计与明细。

## 功能特性

- **定时自动发文**：runner 每 30 秒轮询发布计划，到点自动调用平台发布接口，每天 6 点可生成新一批计划
- **防重复发布**：按标题精确查重，同名文章不重复推送
- **详情 URL 记录**：发布后自动抓取文章详情页真实地址，写入计划文件与当日清单
- **发布台账**：可视化每日/每周/每月/历史累计发文量、分类分布与趋势，支持搜索、筛选、分页
- **数据自动刷新**：每批文章发布完成后自动同步台账数据

## 目录结构

```
├── bot/                      发文 bot
│   ├── publish.py            发布与查重（含分类 ID 映射、token 刷新）
│   ├── runner.py             定时守护主循环（30 秒轮询）
│   ├── fetch_detail_urls.py  发布后从「我的文章」页抓取详情 URL
│   ├── plan.json             发布计划与状态（含 detail_url）
│   ├── HEARTBEAT.md          每日发布清单
│   ├── covers/               文章封面图（800×400）
│   └── token.txt             登录令牌（JWT，约 7 天有效，权限 600）
├── bot-articles/             文章正文（markdown）
├── ledger/                   发布台账展示页
│   ├── index.html            前端页面（Chart.js）
│   ├── fetch_articles.py     数据拉取脚本
│   └── data/articles.json    文章数据（自动生成）
├── .cosmocode/
│   ├── MEMORY.md             项目指令记忆
│   └── docs/                 审计、测试、覆盖证明文档
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

启动后每 30 秒检查一次发布计划（`bot/plan.json`），到点发布、抓取详情 URL、更新 HEARTBEAT 并刷新台账数据。

### 4. 部署台账展示页

```bash
cd ledger && python3 -m http.server 8090 --bind 0.0.0.0
```

浏览器访问 `http://localhost:8090/` 即打开台账首页。需要手动刷新数据时执行：

```bash
python ledger/fetch_articles.py
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
  "cover": "/workspace/bot/covers/cover5.jpg"
}
```

分类可选值见 `bot/publish.py` 的 `CATEGORY_IDS`（支持 17 个分类）。

## 文章规范

- 封面图：800×400，jpg/png，≤1MB
- 正文：markdown，≥1000 字，每段≥200 字，至少 2 张配图（Pexels/Unsplash/Pixabay 免费可商用）
- 标题禁用冒号；正文不得重复标题或二级标题文字
- 需提供约 120 字摘要

## 相关文档

- 安全审计结论：`.cosmocode/docs/安全审计结论.md`
- 测试报告：`.cosmocode/docs/测试报告.md`
- 目标用户全覆盖证明：`.cosmocode/docs/目标用户全覆盖证明.md`
