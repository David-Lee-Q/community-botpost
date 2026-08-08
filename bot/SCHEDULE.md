# bot 定时任务说明（cron）

所有定时任务由 `bot/runner.py` 守护主循环调度（每 30 秒轮询一次，进程内标记避免重复触发）。

## 任务总表

| 触发时间 | 任务 | 执行脚本 | 说明 |
|----------|------|----------|------|
| 每 30 秒 | 发布计划轮询 | `publish.py` → `fetch_detail_urls.py` → `gen_heartbeat` → `fetch_articles.py` | 到点发布 pending 文章；发布后抓详情 URL、更新 HEARTBEAT、刷新台账 |
| 每日 06:00 | 生成新一批计划 | 扩展点 | `runner.py` 预留，可接入计划生成逻辑补充新文章 |
| 每周一 06:00 | 综合评分 | `ledger/review_engine.py` | 计算上一完整周综合分，同步 score_history.json / quality_tasks.md / 质量改进记录.md / MEMORY.md |
| 每日 0/6/12/18 点 | 刷新台账数据 | `ledger/fetch_articles.py` | 每 6 小时一次，含自动评价，保证浏览/互动趋势数据最新 |
| 每日 18:00 | 飞书每日总结 | `report.py daily` | 蓝色卡片：今日发布/浏览/互动 + 文章表现表格 + 综合评分 |
| 每周一 10:00 | 飞书每周总结 | `report.py weekly` | 绿色卡片：本周汇总 + 评分（分档着色）+ 短板 + 改进建议 + 下周待发 |

## 触发条件速览

```python
# runner.py 中的守卫条件
now.hour % 6 == 0            # 每 6 小时刷新数据
now.hour == 18               # 每日 18:00 日报
now.weekday() == 0 and now.hour == 10   # 周一 10:00 周报
now.hour == 6                # 每日 6:00 计划生成（周一同时触发评分）
```

## 修改定时规则

直接编辑 `bot/runner.py` 中对应分支的 `now.hour` / `now.weekday()` 条件，重启 runner 生效：

```bash
pkill -f runner.py
cd /workspace/bot && nohup python3 runner.py > /tmp/runner.log 2>&1 &
```

## 手动触发

```bash
# 立即刷新台账数据
python ledger/fetch_articles.py

# 立即推送每日/每周总结（--preview 仅打印不推送）
python bot/report.py daily
python bot/report.py weekly --preview

# 立即重算综合评分
python ledger/review_engine.py
```
