import json
import os
import re

DATA = os.path.dirname(os.path.abspath(__file__))
ARTICLES_FILE = os.path.join(DATA, "data", "articles.json")
REVIEWS_FILE = os.path.join(DATA, "data", "reviews.json")
CONTENT_SCORES_FILE = os.path.join(DATA, "data", "content_scores.json")

VIEW_W = 0.7
ENGAGE_W = 0.3
VIEW_SCORE_W = 0.4
CONTENT_W = 0.6

WEAK_WORDS = r"赋能|新引擎|新范式|加速落地|驶入快车道|按下快进键|开启新篇章"
CLICHE_OPEN = r"^(在[^，。]{0,18}浪潮|随着[^，。]{0,24}发展|近年来[^，。]{0,10}成为|众所周知|当前[^，。]{0,10}正)"
PAIN_WORDS = r"[0-9０-９]|[？?！!]|为什么|痛点|瓶颈|难题|成本|风险|失败|挑战|焦虑|缺|翻车|惊艳|压缩|别赌|破局|暗藏|避开|坎|荒|数据荒|转型"


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


def content_score(title, summary, body):
    """基于规则的正文内容质量分（0-100），对应《评价标准》内容类维度。"""
    d = {}
    has_weak = bool(re.search(WEAK_WORDS, title))
    has_hook = bool(re.search(PAIN_WORDS, title))
    tlen = len(title)
    if tlen >= 6 and not has_weak:
        d["标题钩子"] = 20 if has_hook else 14
    elif tlen >= 4:
        d["标题钩子"] = 10
    else:
        d["标题钩子"] = 4

    sl = len(summary)
    if 100 <= sl <= 180:
        d["摘要质量"] = 15 if re.search(PAIN_WORDS, summary) else 12
    elif sl >= 60:
        d["摘要质量"] = 8
    else:
        d["摘要质量"] = 2

    blocks = [s for s in body.split("\n\n") if s.strip() and not s.startswith("!")]
    paras = [s for s in blocks if not s.startswith("#")]
    first = paras[0] if paras else ""
    cliche = bool(re.search(CLICHE_OPEN, first))
    if first and not cliche:
        d["首段抓人"] = 20 if re.search(PAIN_WORDS, first) else 12
    elif first:
        d["首段抓人"] = 6
    else:
        d["首段抓人"] = 0

    h2 = len(re.findall(r"^##\s+", body, re.M))
    len_ok = 4 <= len(paras) <= 7 and all(180 <= len(p) <= 450 for p in paras)
    d["结构完整"] = (8 if h2 >= 3 else 0) + (8 if 4 <= len(paras) <= 6 else 3) \
        + (5 if len(paras) else 0) + (4 if len(body) >= 1000 else 0)

    imgs = len(re.findall(r"!\[[^\]]*\]\(https?://", body))
    d["规范合规"] = (5 if not has_weak and ":" not in title else 0) \
        + (5 if imgs >= 2 else (2 if imgs >= 1 else 0))

    d["可读表达"] = (5 if len_ok else 0) + (5 if first and not cliche else 0)

    total = sum(d.values())
    dims = {
        "内容原创性": round(min(20, d["标题钩子"] * 0.9 + d["摘要质量"] * 0.4)),
        "技术准确性": round(min(20, d["结构完整"] * 0.5 + d["首段抓人"] * 0.3)),
        "完整性结构": round(d["结构完整"] / 25 * 20),
        "可读表达": round(d["可读表达"] / 10 * 20),
        "规范合规": round(d["规范合规"] / 10 * 20),
    }
    return total, d, dims


def load_content_scores():
    if not os.path.exists(CONTENT_SCORES_FILE):
        return {}
    return json.load(open(CONTENT_SCORES_FILE, encoding="utf-8"))


def score_entry(aid, title, view_score, cached):
    view_score = max(40, min(95, view_score))
    if cached:
        content_total = cached.get("content_total")
        dims = cached.get("dims") or {}
        score = round(VIEW_SCORE_W * view_score + CONTENT_W * content_total)
        score = max(40, min(95, score))
    else:
        content_total = None
        dims = {}
        score = view_score
    breakdown = {
        "内容原创性": dims.get("内容原创性"), "技术准确性": dims.get("技术准确性"),
        "完整性结构": dims.get("完整性结构"), "可读表达": dims.get("可读表达"),
        "规范合规": dims.get("规范合规"), "传播表现": view_score,
    }
    return score, content_total, breakdown


def main():
    arts = json.load(open(ARTICLES_FILE, encoding="utf-8")).get("articles", [])
    published = [a for a in arts if a.get("status") == 1]
    if not published:
        print("无已发布文章")
        return
    reviews = json.load(open(REVIEWS_FILE, encoding="utf-8"))
    cscores = load_content_scores()
    view_pct = percentile(published, "viewCount")
    engage_pct = percentile(published, "commentCount")

    added, updated = 0, 0
    for a in published:
        aid = str(a["id"])
        if aid in reviews and not reviews[aid].get("auto"):
            continue
        v = a.get("viewCount", 0)
        e = a.get("commentCount", 0) + a.get("favor", 0) + a.get("collect", 0)
        view_score = round(VIEW_W * view_pct.get(v, 0) + ENGAGE_W * engage_pct.get(e, 0))
        cached = cscores.get(aid)
        score, content_total, breakdown = score_entry(aid, a.get("title", ""), view_score, cached)
        grade = grade_of(score)
        new_entry = {
            "title": a.get("title", ""),
            "score": score,
            "grade": grade,
            "auto": True,
            "comment": (f"自动化评价：传播表现百分位 {view_score} 分（浏览 {v}、互动 {e}），"
                        f"内容质量分 {content_total if content_total is not None else '—'}，"
                        f"综合 {score} 分，档位{grade}，详见《社区文章质量评价标准》"),
            "breakdown": breakdown,
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
