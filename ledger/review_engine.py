import datetime
import json
import os

LEDGER = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(LEDGER, "data")
REVIEWS_FILE = os.path.join(DATA, "reviews.json")
ARTICLES_FILE = os.path.join(DATA, "articles.json")
OUT_FILE = os.path.join(DATA, "bot_score.json")

DIM_ORDER = ["内容原创性", "技术准确性", "完整性结构", "可读表达", "规范合规", "传播表现"]

SUGGESTIONS = {
    "内容原创性": "增加一手实践案例与项目数据，减少行业共识性论述，突出增量观点",
    "技术准确性": "为关键数据与结论补充来源或时间标注，交叉核对术语与版本信息",
    "完整性结构": "强化「引言-分论点-结论」框架，每个分论点配数据、案例或引用其一",
    "可读表达": "每段聚焦单一论点并控制段落长度，增强小标题引导与图文呼应",
    "规范合规": "发布前逐项核对平台规范红线（封面尺寸、标题格式、配图版权）",
    "传播表现": "打磨标题信息量与钩子，摘要直击痛点，首段前三句抓住读者",
}


def week_range(today=None):
    today = today or datetime.date.today()
    if today.weekday() == 0:
        end = today - datetime.timedelta(days=1)
        start = end - datetime.timedelta(days=6)
    else:
        start = today - datetime.timedelta(days=today.weekday())
        end = today
    return start, end


def main():
    reviews = json.load(open(REVIEWS_FILE, encoding="utf-8"))
    arts = json.load(open(ARTICLES_FILE, encoding="utf-8")).get("articles", [])
    start, end = week_range()

    in_week = []
    for a in arts:
        if a.get("status") != 1:
            continue
        ct = (a.get("createTime") or "")[:10]
        if not ct:
            continue
        try:
            d = datetime.date.fromisoformat(ct)
        except ValueError:
            continue
        if start <= d <= end and str(a.get("id")) in reviews:
            in_week.append(a)

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not in_week:
        payload = {
            "week": f"{start}~{end}",
            "score": None,
            "articleCount": 0,
            "avgDimensions": {},
            "weakest": [],
            "suggestions": ["本周无已评价的 bot 文章，暂无综合评分"],
            "updatedAt": now_str,
        }
    else:
        scores = [reviews[str(a["id"])]["score"] for a in in_week]
        score = round(sum(scores) / len(scores))
        dim_sums = {d: 0 for d in DIM_ORDER}
        for a in in_week:
            bd = reviews[str(a["id"])].get("breakdown", {})
            for d in DIM_ORDER:
                dim_sums[d] += bd.get(d, 0)
        avg_dims = {d: round(dim_sums[d] / len(in_week), 1) for d in DIM_ORDER}
        weakest = sorted(DIM_ORDER, key=lambda d: avg_dims[d])[:2]
        suggestions = [SUGGESTIONS[d] for d in weakest]
        if min(avg_dims.values()) >= 15:
            suggestions.append("整体质量稳定，建议尝试冲击更细分领域的深度选题")
        payload = {
            "week": f"{start}~{end}",
            "score": score,
            "articleCount": len(in_week),
            "avgDimensions": avg_dims,
            "weakest": weakest,
            "suggestions": suggestions,
            "updatedAt": now_str,
        }

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"综合评分已更新: {payload.get('score')} 分, 周期 {payload['week']}, 文章 {payload['articleCount']} 篇")


if __name__ == "__main__":
    main()
