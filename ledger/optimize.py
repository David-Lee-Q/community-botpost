import datetime
import json
import os
import re
import subprocess
import sys
import time

import requests

LEDGER = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(LEDGER, "data")
ROOT = os.path.dirname(LEDGER)
BOT_DIR = os.path.join(ROOT, "bot")
BASE = "https://openlab.cosmoplat.com/api"

sys.path.insert(0, BOT_DIR)
import one_shot  # noqa: E402
from publish import get_token  # noqa: E402
import sensitive  # noqa: E402

ARTICLES_FILE = os.path.join(DATA, "articles.json")
REVIEWS_FILE = os.path.join(DATA, "reviews.json")
SCORES_FILE = os.path.join(DATA, "article_scores.json")
CONTENT_SCORES_FILE = os.path.join(DATA, "content_scores.json")

OPTIMIZE_PROMPT = (
    "你是卡奥斯开源社区的资深科技文章编辑，负责在不改变原文事实与结构的前提下提升传播表现。"
    "输出必须是合法 JSON 对象，不要输出任何其他文字。JSON 结构："
    '{"title":"优化后标题","summary":"优化后摘要","body":"优化后正文"}。'
    "优化要求（严格执行）："
    "1. 标题信息量大且有钩子：用具体数字、强观点、反差对比或读者痛点，不超过24字、不含冒号，"
    "避免「赋能」「新引擎」「新范式」「加速落地」「驶入快车道」等空泛套话；"
    "2. 摘要约120字：直击读者痛点并点明文章价值，先抛问题或结论；"
    "3. 正文在原内容基础上增强表达："
    "   a. 首段前三句必须抓住读者：用具体数据、真实痛点、反差场景或争议观点切入，"
    "   若原文是「在...浪潮下」「随着...的发展」「近年来」等套话开场，必须重写开场；"
    "   b. 每段聚焦单一论点，段落控制在200-400字，避免并列条目式堆砌；"
    "   c. 二级标题精炼有力、概括段落核心观点，可带数字或反差；"
    "   d. 保留原文事实、案例、结论与逻辑顺序，不虚构数据；"
    "   e. 结尾段给出落地建议或趋势判断；"
    "4. 正文中所有图片标记行（形如 ![xxx](http...)，通常为 ![配图1](...)、![配图2](...)）"
    "必须原样保留位置与链接，不得删除、移动或替换；"
    "5. 正文不少于1000字。"
)


def _load_article(aid):
    token = get_token()
    r = requests.get(f"{BASE}/article/detail/{aid}", headers={"s-user-token": token}, timeout=30)
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError("获取文章详情失败: " + str(d)[:200])
    a = d["data"]
    return {
        "id": a.get("id"),
        "articleId": a.get("articleId") or a.get("id"),
        "title": a.get("title", ""),
        "summary": a.get("description", ""),
        "content": a.get("content", ""),
        "cateId": a.get("cateId"),
        "thumbnail": a.get("thumbnail", ""),
        "source": a.get("source", 1),
        "contentType": a.get("contentType", "markdown"),
        "createTime": a.get("createTime"),
        "viewCount": a.get("viewCount", 0),
        "commentCount": a.get("commentCount", 0),
        "favor": a.get("favor", 0),
        "collect": a.get("collect", 0),
    }


def _parse_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except ValueError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise


def ai_optimize(article):
    src = ("标题：" + article["title"] + "\n\n摘要：" + article["summary"]
           + "\n\n正文：\n" + article["content"])
    for attempt in range(3):
        try:
            content = one_shot._llm([
                {"role": "system", "content": OPTIMIZE_PROMPT},
                {"role": "user", "content": "以下是待优化文章全文，请输出优化稿：\n\n" + src},
            ])
            data = _parse_json(content)
        except Exception as e:
            if attempt < 2:
                continue
            raise RuntimeError("AI优化生成失败，请重试（" + str(e)[:80] + "）")
        title = str(data.get("title", "")).strip()
        summary = str(data.get("summary", "")).strip()
        body = str(data.get("body", "")).strip()
        imgs = re.findall(r"!\[[^\]]*\]\(https?://[^)\s]+\)", body)
        paras = [s for s in body.split("\n\n") if s.strip() and not s.startswith("#")
                 and not s.startswith("!")]
        first_para = paras[0] if paras else ""
        weak_title = re.search(r"(赋能|新引擎|新范式|加速落地|驶入快车道|按下快进键|开启新篇章)", title)
        cliche_open = re.search(
            r"^(在[^，。]{0,18}浪潮|随着[^，。]{0,24}发展|近年来[^，。]{0,10}成为|众所周知|当前[^，。]{0,10}正)", first_para)
        ok = (len(title) >= 6 and not weak_title and not cliche_open
              and len(body) >= 1000 and 100 <= len(summary) <= 160
              and all(180 <= len(s) <= 450 for s in paras)
              and imgs and "IMAGE1" not in body)
        if ok:
            return {"title": title, "summary": summary, "body": body, "images": imgs}
    raise RuntimeError("AI优化未通过规范校验，请重试")


def _save_payload(article, title, summary, content):
    return {
        "canReply": 0, "source": article.get("source", 1), "cateId": article.get("cateId"),
        "activityTopic": None, "activityType": None,
        "contentType": article.get("contentType", "markdown"),
        "sourceLink": "", "thumbnail": article.get("thumbnail", ""),
        "title": title, "description": summary, "content": content,
        "draftId": None, "articleId": article.get("articleId"),
        "id": article.get("id"), "viewRank": 0, "sectionId": None,
    }


def save_update(article, title, summary, content):
    token = get_token()
    payload = _save_payload(article, title, summary, content)
    r = requests.post(
        f"{BASE}/article/save",
        headers={"s-user-token": token, "Content-Type": "application/json"},
        json=payload, timeout=60,
    )
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError("平台保存失败: " + str(d)[:200])
    return d["data"].get("id") or article.get("id")


def _load_reviews():
    if not os.path.exists(REVIEWS_FILE):
        return {}
    return json.load(open(REVIEWS_FILE, encoding="utf-8"))


def _load_scores():
    if not os.path.exists(SCORES_FILE):
        return {}
    return json.load(open(SCORES_FILE, encoding="utf-8"))


def _load_content_scores():
    if not os.path.exists(CONTENT_SCORES_FILE):
        return {}
    return json.load(open(CONTENT_SCORES_FILE, encoding="utf-8"))


def _save_content_score(aid, title, summary, body):
    from auto_review import content_score, save_body, _write_json
    save_body(aid, {"title": title, "summary": summary, "content": body})
    cs = _load_content_scores()
    total, detail, dims = content_score(title, summary, body)
    cs[str(aid)] = {"content_total": total, "detail": detail, "dims": dims,
                    "updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    _write_json(CONTENT_SCORES_FILE, cs)
    return total, dims


def _score_article(aid, arts, live=None):
    published = [a for a in arts if a.get("status") == 1]
    from auto_review import percentile, grade_of, score_entry, load_bodies, fetch_body, save_body
    view_pct = percentile(published, "viewCount")
    engage_pct = percentile(published, "commentCount")
    a = next((x for x in arts if str(x.get("id")) == str(aid)), None)
    if not a:
        return None
    v = (live or a).get("viewCount", 0)
    e = (live or a).get("commentCount", 0) + (live or a).get("favor", 0) + (live or a).get("collect", 0)
    view_score = round(0.7 * view_pct.get(v, 0) + 0.3 * engage_pct.get(e, 0))
    body = load_bodies().get(str(aid))
    if not body:
        try:
            body = fetch_body(aid)
            save_body(aid, body)
        except Exception:
            body = None
    total, dims = score_entry(
        str(aid), a.get("title", ""), view_score,
        body=body.get("content") if body else None,
        summary=body.get("summary") if body else None,
    )
    grade = grade_of(total)
    dim_txt = "，".join(f"{d}{dims[d]}" for d in dims)
    return {
        "title": a.get("title", ""),
        "score": total, "grade": grade, "auto": True,
        "viewScore": view_score,
        "comment": (f"自动化评价：按维度计分（{dim_txt}），总分 {total}，"
                    f"档位{grade}，详见《社区文章质量评价标准》"),
        "breakdown": {d: dims.get(d) for d in dims},
    }


def record_review(aid, entry, old=None, kind="重评"):
    """写回 reviews.json 并记录评分历史，随后刷新综合评分。"""
    reviews = _load_reviews()
    scores = _load_scores()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    hist = scores.get(str(aid), [])
    if old and old.get("score") != entry["score"]:
        hist.append({
            "time": now, "score": old.get("score"), "grade": old.get("grade"),
            "comment": old.get("comment", ""), "breakdown": old.get("breakdown", {}),
            "kind": "优化前",
        })
    hist.append({
        "time": now, "score": entry["score"], "grade": entry["grade"],
        "comment": entry.get("comment", ""), "breakdown": entry.get("breakdown", {}),
        "kind": kind,
    })
    hist = hist[-30:]
    scores[str(aid)] = hist
    reviews[str(aid)] = entry
    from auto_review import _write_json
    _write_json(REVIEWS_FILE, reviews)
    _write_json(SCORES_FILE, scores)
    try:
        subprocess.run([sys.executable, os.path.join(LEDGER, "review_engine.py")],
                       capture_output=True, timeout=60)
    except Exception:
        pass
    return entry, hist


def re_review(aid, old=None, kind="重评"):
    arts = json.load(open(ARTICLES_FILE, encoding="utf-8")).get("articles", [])
    entry = _score_article(aid, arts)
    if not entry:
        raise RuntimeError("台账中未找到该文章，请先刷新数据")
    if old is None:
        old = _load_reviews().get(str(aid))
    return record_review(aid, entry, old=old, kind=kind)


def get_history(aid):
    scores = _load_scores()
    return scores.get(str(aid), [])


def update_and_review(aid, title, summary, content):
    article = _load_article(aid)
    hits = sensitive.check(title) + sensitive.check(summary) + sensitive.check(content)
    if hits:
        sensitive.log_record({
            "source": "文章优化", "article_title": title, "hits": sorted(set(hits)),
            "action": "拒绝保存（内容含敏感词）",
        })
        raise RuntimeError("内容包含敏感词，无法保存：" + "、".join(sorted(set(hits))))
    save_update(article, title, summary, content)
    _save_content_score(aid, title, summary, content)
    time.sleep(2)
    old = _load_reviews().get(str(aid))
    subprocess.run([sys.executable, os.path.join(LEDGER, "fetch_articles.py")],
                   capture_output=True, timeout=300)
    entry, hist = re_review(aid, old=old)
    return {"article_id": aid, "score": entry["score"], "grade": entry["grade"],
            "history": hist}


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "detail":
        print(json.dumps(_load_article(sys.argv[2]), ensure_ascii=False, indent=2))
    elif cmd == "optimize":
        art = _load_article(sys.argv[2])
        print(json.dumps(ai_optimize(art), ensure_ascii=False, indent=2))
    elif cmd == "scores":
        print(json.dumps(get_history(sys.argv[2]), ensure_ascii=False, indent=2))
