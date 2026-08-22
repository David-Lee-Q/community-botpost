import datetime
import json
import os
import re
import sys
import argparse
import requests

DATA = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(DATA)
BOT_DIR = os.path.join(ROOT, "bot")
ARTICLES_FILE = os.path.join(DATA, "data", "articles.json")
REVIEWS_FILE = os.path.join(DATA, "data", "reviews.json")
SCORES_FILE = os.path.join(DATA, "data", "article_scores.json")
BODIES_FILE = os.path.join(DATA, "data", "article_bodies.json")
BASE = "https://openlab.cosmoplat.com/api"

sys.path.insert(0, BOT_DIR)
from publish import get_token  # noqa: E402

VIEW_W = 0.7
ENGAGE_W = 0.3

DIM_ORDER = ["内容原创性", "技术准确性", "完整性结构", "可读表达", "规范合规", "传播表现"]

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


def _text_metrics(title, summary, body):
    tlen = len(title)
    slen = len(summary)
    has_weak = bool(re.search(WEAK_WORDS, title))
    has_colon = ":" in title
    has_hook = bool(re.search(PAIN_WORDS, title))
    sum_hook = bool(re.search(PAIN_WORDS, summary))
    blocks = [s for s in body.split("\n\n") if s.strip() and not s.startswith("!")]
    paras = [s for s in blocks if not s.startswith("#")]
    first = paras[0] if paras else ""
    cliche = bool(re.search(CLICHE_OPEN, first))
    h2 = len(re.findall(r"^##\s+", body, re.M))
    imgs = len(re.findall(r"!\[[^\]]*\]\(https?://", body))
    bodylen = len(body)
    num_hits = len(re.findall(r"[0-9０-９]", body))
    len_ok = 4 <= len(paras) <= 7 and all(180 <= len(p) <= 450 for p in paras)
    return {
        "tlen": tlen, "slen": slen, "has_weak": has_weak, "has_colon": has_colon,
        "has_hook": has_hook, "sum_hook": sum_hook, "paras": paras, "first": first,
        "cliche": cliche, "h2": h2, "imgs": imgs, "bodylen": bodylen,
        "num_hits": num_hits, "len_ok": len_ok,
    }


def score_dims(title, summary, body, view_score=None, has_body=True):
    """按《社区文章质量评价标准》6 维度一一打分，各维按满分(20/20/15/15/15/15)，total=加权总分(0-100)。"""
    m = _text_metrics(title, summary, body)
    detail = {}
    if has_body:
        orig = 0
        orig += 8 if (m["tlen"] >= 6 and not m["has_weak"]) else (4 if m["tlen"] >= 4 else 1)
        orig += 6 if (m["slen"] >= 60 and m["sum_hook"]) else (3 if m["slen"] >= 60 else 1)
        orig += 6 if m["num_hits"] >= 8 else (3 if m["num_hits"] >= 3 else 1)
        orig = min(20, orig)
        detail["内容原创性"] = {"标题去套话有信息": orig >= 12, "摘要具体": m["slen"] >= 60, "数据/案例密度": m["num_hits"]}

        tech = 0
        tech += 8 if m["num_hits"] >= 8 else (4 if m["num_hits"] >= 3 else 1)
        tech += 6 if (m["h2"] >= 2 and len(m["paras"]) >= 3) else 3
        tech += 6 if not m["cliche"] else 2
        tech = min(20, tech)
        detail["技术准确性"] = {"数据可溯源感": m["num_hits"], "分层论述": m["h2"], "无套话硬伤": not m["cliche"]}

        struct = 0
        struct += 5 if m["h2"] >= 3 else (2 if m["h2"] >= 1 else 0)
        struct += 5 if 4 <= len(m["paras"]) <= 6 else (2 if len(m["paras"]) >= 2 else 0)
        struct += 5 if m["bodylen"] >= 1000 else (2 if m["bodylen"] >= 600 else 0)
        struct = min(15, struct)
        detail["完整性结构"] = {"小标题数": m["h2"], "段落数": len(m["paras"]), "字数": m["bodylen"]}

        read = 0
        read += 5 if m["len_ok"] else (2 if len(m["paras"]) >= 2 else 0)
        read += 5 if (m["first"] and not m["cliche"]) else (2 if m["first"] else 0)
        read += 5 if m["imgs"] >= 2 else (2 if m["imgs"] >= 1 else 0)
        read = min(15, read)
        detail["可读表达"] = {"段落节奏": m["len_ok"], "首段抓人": not m["cliche"], "配图数": m["imgs"]}

        comp = 0
        comp += 5 if (not m["has_weak"] and not m["has_colon"]) else (2 if not m["has_colon"] else 0)
        comp += 5 if 100 <= m["slen"] <= 180 else (2 if m["slen"] >= 40 else 0)
        comp += 5 if m["imgs"] >= 2 else (3 if m["imgs"] >= 1 else 1)
        comp = min(15, comp)
        detail["规范合规"] = {"标题合规": not m["has_weak"] and not m["has_colon"], "摘要长度": m["slen"], "配图数": m["imgs"]}
    else:
        orig = 0
        orig += 8 if (m["tlen"] >= 6 and not m["has_weak"]) else (4 if m["tlen"] >= 4 else 1)
        orig += 6 if (m["slen"] >= 60 and m["sum_hook"]) else (3 if m["slen"] >= 60 else 1)
        orig += 3
        tech = 8
        struct = 6
        read = 6
        comp = (5 if (not m["has_weak"] and not m["has_colon"]) else 2) \
            + (5 if 100 <= m["slen"] <= 180 else 2) + 2
        comp = min(15, comp)
        detail["内容原创性"] = {"正文未获取": True}
        detail["技术准确性"] = {"正文未获取": True}
        detail["完整性结构"] = {"正文未获取": True}
        detail["可读表达"] = {"正文未获取": True}
        detail["规范合规"] = {"正文未获取": True}

    if view_score is not None:
        spread = round(10 * max(0, min(100, view_score)) / 100)
        spread += 5 if m["has_hook"] else (3 if m["tlen"] >= 6 else 1)
    else:
        spread = 5 if m["has_hook"] else (3 if m["tlen"] >= 6 else 1)
    spread = min(15, spread)
    detail["传播表现"] = {"传播百分位": view_score, "标题钩子": m["has_hook"]}

    dims = {
        "内容原创性": orig, "技术准确性": tech, "完整性结构": struct,
        "可读表达": read, "规范合规": comp, "传播表现": spread,
    }
    total = sum(dims.values())
    return total, detail, dims


def content_score(title, summary, body):
    """兼容旧接口：返回 (total, detail, dims)，dims 为 6 维。"""
    return score_dims(title, summary, body, view_score=None, has_body=bool(body))


def load_bodies():
    if not os.path.exists(BODIES_FILE):
        return {}
    return json.load(open(BODIES_FILE, encoding="utf-8"))


def save_body(aid, body):
    bodies = load_bodies()
    bodies[str(aid)] = {
        "title": body.get("title", ""), "summary": body.get("summary", ""),
        "content": body.get("content", ""),
        "updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(BODIES_FILE, "w", encoding="utf-8") as f:
        json.dump(bodies, f, ensure_ascii=False, indent=2)


def fetch_body(aid):
    token = get_token()
    r = requests.get(f"{BASE}/article/detail/{aid}", headers={"s-user-token": token}, timeout=30)
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError("获取正文失败: " + str(d)[:120])
    a = d["data"]
    return {
        "title": a.get("title", ""), "summary": a.get("description", ""),
        "content": a.get("content", ""),
    }


def score_entry(aid, title, view_score, body=None, summary=None):
    if not body:
        cached = load_bodies().get(str(aid))
        body = cached.get("content") if cached else None
        summary = summary or (cached.get("summary") if cached else "")
    has_body = bool(body)
    total, detail, dims = score_dims(title, summary or "", body or "", view_score, has_body)
    return total, dims


def main(force=False):
    arts = json.load(open(ARTICLES_FILE, encoding="utf-8")).get("articles", [])
    published = [a for a in arts if a.get("status") == 1]
    if not published:
        print("无已发布文章")
        return
    reviews = json.load(open(REVIEWS_FILE, encoding="utf-8"))
    if os.path.exists(SCORES_FILE):
        scores = json.load(open(SCORES_FILE, encoding="utf-8"))
    else:
        scores = {}
    bodies = load_bodies()
    view_pct = percentile(published, "viewCount")
    engage_pct = percentile(published, "commentCount")

    added, updated = 0, 0
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for a in published:
        aid = str(a["id"])
        v = a.get("viewCount", 0)
        e = a.get("commentCount", 0) + a.get("favor", 0) + a.get("collect", 0)
        prev = reviews.get(aid)
        if not force and prev and prev.get("auto"):
            same = (prev.get("viewCount") == v
                    and prev.get("commentCount") == a.get("commentCount", 0)
                    and prev.get("favor") == a.get("favor", 0)
                    and prev.get("collect") == a.get("collect", 0))
            if same and aid in bodies and prev.get("breakdown"):
                continue

        view_score = round(VIEW_W * view_pct.get(v, 0) + ENGAGE_W * engage_pct.get(e, 0))
        body = bodies.get(aid)
        if not body:
            try:
                body = fetch_body(aid)
                save_body(aid, body)
                bodies[aid] = body
            except Exception as ex:
                print(f"正文获取失败 aid={aid}: {str(ex)[:100]}")
                body = None

        total, dims = score_entry(
            aid, a.get("title", ""), view_score,
            body=body.get("content") if body else None,
            summary=body.get("summary") if body else None,
        )
        grade = grade_of(total)
        breakdown = {d: dims.get(d) for d in DIM_ORDER}
        dim_txt = "，".join(f"{d}{dims[d]}" for d in DIM_ORDER)
        entry = {
            "title": a.get("title", ""),
            "score": total, "grade": grade, "auto": True,
            "time": now,
            "viewScore": view_score,
            "viewCount": v, "commentCount": a.get("commentCount", 0),
            "favor": a.get("favor", 0), "collect": a.get("collect", 0),
            "comment": (f"自动化评价：按维度计分（{dim_txt}），总分 {total}，"
                        f"档位{grade}，详见《社区文章质量评价标准》"),
            "breakdown": breakdown,
        }
        if aid in reviews:
            updated += 1
        else:
            added += 1
        reviews[aid] = entry

        hist = scores.get(aid, [])
        is_auto = lambda h: h.get("kind") == "自动评价" or str(h.get("comment", "")).startswith("自动化评价")
        if hist and is_auto(hist[-1]):
            hist[-1] = {"time": now, "score": total, "grade": grade,
                        "comment": entry["comment"], "breakdown": breakdown, "kind": "自动评价"}
        else:
            hist.append({"time": now, "score": total, "grade": grade,
                         "comment": entry["comment"], "breakdown": breakdown, "kind": "自动评价"})
        hist = hist[-30:]
        scores[aid] = hist

    with open(REVIEWS_FILE, "w", encoding="utf-8") as f:
        json.dump(reviews, f, ensure_ascii=False, indent=2)
    with open(SCORES_FILE, "w", encoding="utf-8") as f:
        json.dump(scores, f, ensure_ascii=False, indent=2)
    print(f"自动化评价完成: 新增 {added} 篇, 更新 {updated} 篇, 已发布总数 {len(published)}")
    auto = sum(1 for r in reviews.values() if r.get("auto"))
    manual = len(reviews) - auto
    print(f"评价覆盖: 人工 {manual} 篇 + 自动 {auto} 篇 = {len(reviews)} 篇")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="忽略幂等判断，强制重评全部已发布文章")
    args = ap.parse_args()
    main(force=args.force)
