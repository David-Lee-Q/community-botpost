import datetime
import json
import os
import sys

import requests

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BOT_DIR)
WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/4b1f67f6-b4f8-4ca0-b5c9-99c0141c676c"
ARTICLES = os.path.join(ROOT, "ledger", "data", "articles.json")
SCORE_HISTORY = os.path.join(ROOT, "ledger", "data", "score_history.json")
PLAN = os.path.join(BOT_DIR, "plan.json")
QUALITY_TASKS = os.path.join(ROOT, ".cosmocode", "quality_tasks.md")

CATE_MAP = {
    1: "智能制造", 2: "云计算", 3: "云原生", 4: "物联网", 5: "边缘计算",
    6: "人工智能", 7: "大数据", 8: "区块链", 9: "标识解析", 10: "中间件",
    11: "微服务", 12: "安全", 13: "编程与开发", 14: "网络", 15: "机器视觉",
    -1: "工业操作系统", -2: "数据要素",
}


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _articles():
    d = _load(ARTICLES)
    return d.get("articles", [])


def _view_engage(a):
    return (
        a.get("viewCount", 0),
        a.get("commentCount", 0) + a.get("favor", 0) + a.get("collect", 0),
    )


def _monday(dt):
    return dt - datetime.timedelta(days=dt.weekday())


def daily_text(day=None):
    day = day or datetime.date.today()
    arts = _articles()
    today_arts = [a for a in arts if (a.get("createTime") or "").startswith(day.isoformat())]
    published = [a for a in today_arts if a.get("status") == 1]
    total_view = sum(a.get("viewCount", 0) for a in published)
    total_engage = sum(
        a.get("commentCount", 0) + a.get("favor", 0) + a.get("collect", 0) for a in published
    )
    sh = _load(SCORE_HISTORY).get("history") or []
    last_score = sh[0] if sh else None
    plan = _load(PLAN).get("schedule", [])
    pending = [it for it in plan if it.get("status") == "pending"
               and it.get("time") and it["time"].startswith(day.isoformat())]

    lines = []
    lines.append("【社区内容自运营智能体】每日总结")
    lines.append("日期：" + day.strftime("%Y-%m-%d"))
    lines.append("")
    lines.append("今日发布：" + str(len(published)) + " 篇")
    lines.append("今日浏览量：" + str(total_view))
    lines.append("今日互动次数：" + str(total_engage) + "（评论+点赞+收藏）")
    if published:
        lines.append("")
        lines.append("今日文章表现：")
        for a in sorted(published, key=lambda x: -_view_engage(x)[0])[:5]:
            v, e = _view_engage(a)
            cate = CATE_MAP.get(a.get("cateId"), "")
            lines.append("- " + str(a.get("title", ""))[:30] + "（" + cate + "）浏览 " + str(v) + "，互动 " + str(e))
    if last_score:
        lines.append("")
        lines.append("综合评分：" + str(last_score["score"]) + " 分（周期 " + last_score["week"] + "）")
    if pending:
        lines.append("")
        lines.append("今日计划待发：" + str(len(pending)) + " 篇")
    return "\n".join(lines)


def weekly_text(week_end=None):
    week_end = week_end or datetime.date.today()
    mon = _monday(week_end)
    arts = _articles()
    week_arts = [a for a in arts if mon.isoformat() <= (a.get("createTime") or "")[:10] <= week_end.isoformat()]
    published = [a for a in week_arts if a.get("status") == 1]
    total_view = sum(a.get("viewCount", 0) for a in published)
    total_engage = sum(
        a.get("commentCount", 0) + a.get("favor", 0) + a.get("collect", 0) for a in published
    )
    sh = _load(SCORE_HISTORY).get("history") or []
    last_score = sh[0] if sh else None
    plan = _load(PLAN).get("schedule", [])
    pending = [it for it in plan if it.get("status") == "pending"]

    lines = []
    lines.append("【社区内容自运营智能体】每周总结")
    lines.append("周期：" + mon.strftime("%Y-%m-%d") + " ~ " + week_end.strftime("%Y-%m-%d"))
    lines.append("")
    lines.append("本周发布：" + str(len(published)) + " 篇")
    lines.append("本周浏览量：" + str(total_view))
    lines.append("本周互动次数：" + str(total_engage) + "（评论+点赞+收藏）")
    if last_score:
        lines.append("")
        lines.append("综合评分：" + str(last_score["score"]) + " 分（覆盖 " + str(last_score["articleCount"]) + " 篇 bot 文章）")
        if last_score.get("weakest"):
            lines.append("短板维度：" + "、".join(last_score["weakest"]))
        for s in (last_score.get("suggestions") or [])[:3]:
            lines.append("改进建议：" + s)
    lines.append("")
    lines.append("下周待发布：" + str(len(pending)) + " 篇")
    return "\n".join(lines)


def push(text):
    r = requests.post(WEBHOOK, json={"msg_type": "text", "content": {"text": text}}, timeout=15)
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError("飞书推送失败: " + json.dumps(data, ensure_ascii=False))
    return data


def main():
    kind = sys.argv[1] if len(sys.argv) > 1 else "daily"
    preview = "--preview" in sys.argv
    if kind == "daily":
        text = daily_text()
    elif kind == "weekly":
        text = weekly_text()
    else:
        print("用法: report.py daily|weekly [--preview]")
        return 1
    print(text)
    if not preview:
        push(text)
        print("已推送飞书")
    return 0


if __name__ == "__main__":
    sys.exit(main())
