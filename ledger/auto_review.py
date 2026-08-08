import json
import os

DATA = os.path.dirname(os.path.abspath(__file__))
ARTICLES_FILE = os.path.join(DATA, "data", "articles.json")
REVIEWS_FILE = os.path.join(DATA, "data", "reviews.json")

VIEW_W = 0.7
ENGAGE_W = 0.3


def percentile(articles, key):
    vals = sorted((a.get(key, 0) for a in articles), reverse=True)
    n = len(vals)
    if n == 0:
        return {}
    out = {}
    for i, v in enumerate(vals):
        pct = (n - i) / n * 100
        out[v] = out.get(v, pct)
    return out


def grade_of(score):
    if score >= 90:
        return "优秀"
    if score >= 75:
        return "良好"
    if score >= 60:
        return "合格"
    return "不合格"


def main():
    arts = json.load(open(ARTICLES_FILE, encoding="utf-8")).get("articles", [])
    published = [a for a in arts if a.get("status") == 1]
    if not published:
        print("无已发布文章")
        return
    reviews = json.load(open(REVIEWS_FILE, encoding="utf-8"))
    view_pct = percentile(published, "viewCount")
    engage_pct = percentile(published, "commentCount")

    added, updated = 0, 0
    for a in published:
        aid = str(a["id"])
        if aid in reviews and not reviews[aid].get("auto"):
            continue
        v = a.get("viewCount", 0)
        e = a.get("commentCount", 0) + a.get("favor", 0) + a.get("collect", 0)
        vp = view_pct.get(v, 0)
        ep = engage_pct.get(e, 0)
        score = round(VIEW_W * vp + ENGAGE_W * ep)
        score = max(40, min(95, score))
        grade = grade_of(score)
        new_entry = {
            "title": a.get("title", ""),
            "score": score,
            "grade": grade,
            "auto": True,
            "comment": (f"自动化评价：按传播表现百分位评定（浏览 {v}、互动 {e}），"
                        f"评分 {score}，档位{grade}，详见《社区文章质量评价标准》"),
            "breakdown": {
                "内容原创性": None, "技术准确性": None, "完整性结构": None,
                "可读表达": None, "规范合规": None, "传播表现": score,
            },
        }
        if aid in reviews:
            updated += 1
        else:
            added += 1
        reviews[aid] = new_entry

    with open(REVIEWS_FILE, "w", encoding="utf-8") as f:
        json.dump(reviews, f, ensure_ascii=False, indent=2)
    print(f"自动化评价完成: 新增 {added} 篇, 更新 {updated} 篇, 已发布总数 {len(published)}")
    auto = sum(1 for r in reviews.values() if r.get("auto"))
    manual = len(reviews) - auto
    print(f"评价覆盖: 人工 {manual} 篇 + 自动 {auto} 篇 = {len(reviews)} 篇")


if __name__ == "__main__":
    main()
