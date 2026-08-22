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
  - bot 组件位于 /workspace/bot：publish.py（发布+查重）、runner.py（定时守护，30秒轮询，调度发布/评分/数据刷新/飞书报告）、report.py（每日/每周总结飞书推送）、fetch_detail_urls.py（抓取详情URL）、plan.json（发布计划）、HEARTBEAT.md（发布记录）、SCHEDULE.md（定时任务说明）
  - 发布直接调用 POST /api/article/save，认证用自定义 header `s-user-token`（JWT，有效期约7天），token 存于 /workspace/bot/token.txt，失效时 runner 通过 Playwright 重新登录获取
  - 文章封面上传：POST /api/file/uploadFileStream?type=1（multipart），返回 data.path 作为 thumbnail
  - 发布必填字段：cateId（分类ID，见 publish.py CATEGORY_IDS）、thumbnail、title、description（摘要）、content（markdown正文，不含首行#标题）、viewRank=0
  - 封面要求 800×400、jpg/png、≤1MB；正文配图需至少2张，免费可商用图源见「配图来源规范」条目

[内容源目录]
- Date: 2026-08-10
- Context: 用户提供内容源审计报告要求落地目录，注意去重
- Category: 工作流协作
- Instructions:
  - 结构化配置位于 /workspace/bot/content_sources.json（来源 audit_report=documents/source-audit-2026-07-10.md）
  - 36 个有效源：第一梯队25（RSS/API/HTML 直通，web_fetch 直接请求）、第二梯队6（web_fetch 可用）、第三梯队5（量子位/极客公园/CSDN AI/ModelScope/腾讯混元，browser 兜底）；15 个不可用源在 unavailable 字段
  - 去重：原清单中 InfoQ 英文站、机器之心同时出现在第三梯队与不可用列表，已归类为不可用；全部条目 URL 唯一
  - 采集去重规则：跨源标题去重（归一化标题仅采1次）、URL/标题 hash 去重、24 小时内不重复、多源转载优先权威源
  - 2026-08-10 复验：29/31 个第一二梯队源可达（github.com 网页直连超时，建议用 web_fetch 或 api.github.com）；api.github.com 均正常

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
- Date: 2026-08-22
- Context: review_engine.py 每周一基于综合评分自动沉淀优化建议
- Instructions:
  - 每周一更新综合评分后，自动同步优化建议到 .cosmocode/docs/质量改进记录.md 与 .cosmocode/quality_tasks.md
  - 最新周期 2026-08-17~2026-08-22 综合分 90，当前短板维度：传播表现、完整性结构
  - 改进要点：打磨标题信息量与钩子，摘要直击痛点，首段前三句抓住读者
  - 改进要点：强化「引言-分论点-结论」框架，每个分论点配数据、案例或引用其一
  - 创作新文章前，先核对 quality_tasks.md 的未完成改进项并主动应用
  - 每周至少一次：将 quality_tasks.md 的未完成改进项落地到 one_shot.py 的 SYSTEM_PROMPT（文章撰写工作流），完成后把对应项改为 [x] 并注明完成日期，形成质量正向循环
  - 周综合评分低于 85 时，下批文章发布前必须优先落实对应改进要点

[配图来源规范]
- Date: 2026-08-10
- Context: 用户明确正文配图与封面图的来源与处理方式，并因文章配图重复要求扩展图源
- Instructions:
  - 正文配图从以下网站检索并确认所用为免费图（非付费/非授权受限），正文中直接引用图片链接：https://www.magnific.com/、https://www.pexels.com/zh-cn/、https://unsplash.com/、https://pixabay.com/
  - 封面图必须下载到工作区再上传，保存目录按文章主题区分：/workspace/bot/covers/<主题关键词>/（如 ai、smart-manufacturing、cloud-native、industrial-internet）
  - 封面图按主题归类，避免同一张图在多篇文章中混用
  - 2026-08-10 配图去重机制：IMAGE_POOL 为多源图池（101张=51 Unsplash+50 Pexels，均已批量验证直链可达），每次发文随机取3张（正文2+封面1），全局去重记录于 bot/used_images.json（site:ref 格式，池耗尽自动重置轮换）
  - 2026-08-10 配图主题化：因配图与内容不相关，改为主题化池——IMAGE_POOL 73 张全部为记忆确认主题的 Unsplash 图，按 theme 分组（ai 11/code 9/dc 12/ind 15/data 9/abs 17）；THEMES 映射文章分类→主题组，pick_images(category) 优先分类主题组取图、不足时扩展相邻组、全池耗尽重置（保留 recent 最近9张避免立即重复）；Pexels 随机扫描图（主题未知）已移除
  - 主题分组：ai=机器人/AI/对话，code=代码编程，dc=数据中心/服务器/芯片，ind=工业制造/自动化，data=数据图表/分析，abs=抽象科技/网络；LLM 无视觉能力、Pexels/Unsplash 检索与详情页均反爬，故主题标签靠记忆标注并逐一验证可达性
  - 图源可达性结论：images.unsplash.com 与 images.pexels.com 直链均可用，平台会抓取正文图转存 hd-oss.cosmoplat.com（无需担心外链失效）；magnific.com 是 AI 放大工具无免费图库；pixabay 搜索需 API key 且部分 CDN 图 hotlink 403，故暂未纳入池
  - 发一篇配图流程：generate() 正文保留 IMAGE1/IMAGE2 占位→run() 调 pick_images() 选图替换占位并下载封面→发布成功后将3张图 keys 记入 used_images.json 对应文章

[飞书报告定时推送]
- Date: 2026-08-08
- Context: 用户要求配置每日/每周总结定时推送到飞书，并优化卡片样式
- Instructions:
  - 每日 18:00 推送每日总结，每周一 10:00 推送每周总结（runner.py 定时触发）
  - 脚本 `bot/report.py`（daily/weekly，`--preview` 仅打印不推送）；Webhook 常量在 report.py 顶部
  - 样式：interactive 卡片，日报蓝色 / 周报绿色 header；文章表现用 `column_set` 表格（# / 标题[超链接] / 浏览-互动），标题链接格式 `https://openlab.cosmoplat.com/article-detils?id={id}&articleType=0`；综合评分按 85/70 分档着色（绿/橙/红）
  - 所有定时任务清单见 `bot/SCHEDULE.md`；分类名映射以 `GET /api/cate/list` 返回为准（id 含 -1 工业操作系统、-2 数据要素）

[定时发布排期与计划生成机制]
- Date: 2026-08-13
- Context: 用户要求每天保持 4 篇定时发文；8-8 后定时发布曾因计划池耗尽（gen_plan 扩展点未实现）停摆，已修复
- Category: 运维部署
- Instructions:
  - 每天定时发布 4 篇，时段 09:00/12:00/15:00/18:00，未来 3 天计划由 bot/gen_plan.py 自动补足（gen_plan.py 的 PUBLISH_TIMES/DAILY_COUNT 配置）
  - gen_plan.py：按 17 分类选题池用 LLM 生成标题/摘要/正文，复用 one_shot 主题图池配图+封面，写入 plan.json（status=pending）；封面下载失败自动换图重试（Unsplash 个别图已失效 404）
  - runner.py 每天 6 点调用 gen_plan.py 补足计划；到点由 runner 触发 publish.py 发布并登记台账
  - 调整排期配置后需清理 plan.json 中旧时段 pending 条目再重新生成，避免单日超过设定篇数

[不合格文章自动优化流程]
- Date: 2026-08-10
- Context: 用户要求评分<60的bot文章自动执行AI优化并重新评分直至达标
- Instructions:
  - 触发：runner.py 每6小时刷新台账后调用 ledger/automate_optimize.py，扫描 bot_posts.json 中评分<60的bot文章
  - 流程：AI优化→平台save更新→存内容分缓存→fetch刷新→重评，每篇最多3轮；达标(≥60)或达上限即停
  - 评分机制：auto 评分 = 0.4*传播百分位 + 0.6*内容质量分(规则化)；传播分 clamp 40-95；内容分仅对优化过的文章启用（content_scores.json 缓存，articles.json 无正文无法全量计算）
  - bot 文章台账 ledger/data/bot_posts.json：发一篇/定时发文发布成功后自动注册（one_shot.py/publish.py 的 _register_post）
  - 评分历史 ledger/data/article_scores.json：每次优化/重评记录（kind=优化前/自动优化第N轮/重评），前端「历史」按钮展示
  - 文章优化API在 server.py：GET /api/article?id=、POST /api/optimize、POST /api/update、GET /api/scores?id=；AI优化复用 one_shot._llm，保留原图片标记
  - 平台更新接口：POST /api/article/save 带 id/articleId 即更新已发布文章，更新后 status 短暂为0约2秒自动恢复，公开访问不受影响；正文获取用 GET /api/article/detail/{id}

[bot 守护进程排障要点]
- Date: 2026-08-20
- Context: Agent 排查 8-19 无发文（runner 崩溃停机）时掌握的运维知识
- Category: 排错调试
- Instructions:
  - runner.py 的 __main__ 已加 while True+try/except 兜底：主循环任何异常（含 subprocess 超时）30 秒后自动重启 main()，不再整体退出；单次子任务崩溃会重建循环内状态变量，可能重复触发一次子任务属可接受
  - publish.py 超时已从 300s 提高到 900s；积压过期 pending 会每 6 分钟反复触发发布且 300s 处理不完，导致 runner 崩溃，必须先清理 plan.json 过期 pending 再补计划
  - gen_plan.py 已改为每篇排期成功立即落盘 plan.json；单篇 LLM 生成失败（如 JSONDecodeError）捕获跳过该时段继续，不再整体崩溃丢进度
  - 恢复发布流程：POST /api/article/list 验证 token(code=0)→清理 plan.json 过期 pending→python3 gen_plan.py 3 补计划（12篇约40-60分钟）→重启 runner
  - runner 启动优先用后台终端方式运行（本环境 shell 工具下 nohup 后台进程会被清理）
