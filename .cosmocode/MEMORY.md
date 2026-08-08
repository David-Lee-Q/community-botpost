# 用户指令记忆

本文件记录了用户的指令、偏好和教导，用于在未来的交互中提供参考。

## 格式

### 用户指令条目
用户指令条目应遵循以下格式：

[用户指令摘要]
- Date: [YYYY-MM-DD]
- Context: [提及的场景或时间]
- Instructions:
  - [用户教导或指示的内容，逐行描述]

### 项目知识条目
Agent 在任务执行过程中发现的条目应遵循以下格式：

[项目知识摘要]
- Date: [YYYY-MM-DD]
- Context: Agent 在执行 [具体任务描述] 时发现
- Category: [运维部署|构建方法|测试方法|排错调试|工作流协作|环境配置]
- Instructions:
  - [具体的知识点，逐行描述]

## 去重策略
- 添加新条目前，检查是否存在相似或相同的指令
- 若发现重复，跳过新条目或与已有条目合并
- 合并时，更新上下文或日期信息
- 这有助于避免冗余条目，保持记忆文件整洁

## 条目

[社区发文记录详情 URL]
- Date: 2026-08-08
- Context: 用户要求每天发布完成后记录文章详情页 URL
- Instructions:
  - 每批文章发布完成后，必须从「我的文章」页（https://openlab.cosmoplat.com/usercenter/1447/article/全部文章）按标题查询文章，点击进入详情页，从浏览器地址栏获取真实详情 URL 并记录
  - 详情 URL 格式：https://openlab.cosmoplat.com/article-detils?id={文章ID}&articleType=0
  - 以 bot 发布接口返回的 article_id 为准记录 URL（列表页存在同名/重复文章，避免误记）
  - 记录位置：/workspace/bot/plan.json 的 detail_url 字段 与 /workspace/bot/HEARTBEAT.md 当日清单

[社区发文 bot 运行方式]
- Date: 2026-08-08
- Context: Agent 构建社区自动发文 bot 时掌握的环境知识
- Category: 运维部署
- Instructions:
  - bot 组件位于 /workspace/bot：publish.py（发布+查重）、runner.py（定时守护，30秒轮询）、fetch_detail_urls.py（抓取详情URL）、plan.json（发布计划）、HEARTBEAT.md（发布记录）
  - 发布直接调用 POST /api/article/save，认证用自定义 header `s-user-token`（JWT，有效期约7天），token 存于 /workspace/bot/token.txt，失效时 runner 通过 Playwright 重新登录获取
  - 文章封面上传：POST /api/file/uploadFileStream?type=1（multipart），返回 data.path 作为 thumbnail
  - 发布必填字段：cateId（分类ID，见 publish.py CATEGORY_IDS）、thumbnail、title、description（摘要）、content（markdown正文，不含首行#标题）、viewRank=0
  - 封面要求 800×400、jpg/png、≤1MB；正文配图需至少2张，可用 Unsplash 直链

[发布台账展示页]
- Date: 2026-08-08
- Context: Agent 构建社区发文台账前端展示页时发现
- Category: 运维部署
- Instructions:
  - 台账项目位于 /workspace/ledger：index.html（前端页面）+ fetch_articles.py（数据拉取脚本）+ data/articles.json（数据）
  - 数据源：POST /api/member/article/1447?pageNum=N&pageSize=100，返回账号全部文章（含 cateId/createTime/status/viewCount/commentCount/favor/collect），total 为总量
  - 分类名映射从 GET /api/cate/list 获取 id→name
  - 静态服务器：/workspace/ledger 下 `python3 -m http.server 8090 --bind 0.0.0.0`，根路径即首页 index.html
  - 发布完成后 runner.py 会自动调用 fetch_articles.py 刷新台账数据，无需手动拉取

[发文质量持续改进]
- Date: 2026-08-08
- Context: review_engine.py 每周一基于综合评分自动沉淀优化建议
- Instructions:
  - 每周一更新综合评分后，自动同步优化建议到 .cosmocode/docs/质量改进记录.md 与 .cosmocode/quality_tasks.md
  - 最新周期 2026-08-03~2026-08-08 综合分 88，当前短板维度：传播表现、可读表达
  - 改进要点：打磨标题信息量与钩子，摘要直击痛点，首段前三句抓住读者
  - 改进要点：每段聚焦单一论点并控制段落长度，增强小标题引导与图文呼应
  - 创作新文章前，先核对 quality_tasks.md 的未完成改进项并主动应用
  - 周综合评分低于 85 时，下批文章发布前必须优先落实对应改进要点
