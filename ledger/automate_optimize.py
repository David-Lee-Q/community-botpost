import datetime
import json
import os
import subprocess
import sys
import time

LEDGER = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(LEDGER, "data")
ROOT = os.path.dirname(LEDGER)

sys.path.insert(0, LEDGER)
import optimize as opt  # noqa: E402

POSTS_FILE = os.path.join(DATA, "bot_posts.json")
REVIEWS_FILE = os.path.join(DATA, "reviews.json")
ARTICLES_FILE = os.path.join(DATA, "articles.json")
PASS_SCORE = 60
MAX_ROUNDS = 3


def log(msg):
    line = f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)


def load_posts():
    if not os.path.exists(POSTS_FILE):
        return {}
    return json.load(open(POSTS_FILE, encoding="utf-8"))


def candidates():
    reviews = json.load(open(REVIEWS_FILE, encoding="utf-8"))
    posts = load_posts()
    out = []
    for aid in posts:
        if posts[str(aid)].get("source") == "手动":
            continue
        rv = reviews.get(str(aid))
        if rv and rv.get("auto") and (rv.get("score") or 0) < PASS_SCORE:
            out.append((str(aid), posts[str(aid)], rv))
    out.sort(key=lambda x: x[2].get("score", 0))
    return out


def optimize_article(aid, meta, rounds_log):
    article = opt._load_article(aid)
    for round_no in range(1, MAX_ROUNDS + 1):
        log(f"  第{round_no}轮 AI 优化中（{meta.get('title','')[:20]}）...")
        result = opt.ai_optimize(article)
        old = opt._load_reviews().get(str(aid))
        log(f"  第{round_no}轮优化稿: {result['title']}（正文 {len(result['body'])} 字）")
        opt.save_update(article, result["title"], result["summary"], result["body"])
        opt._save_content_score(aid, result["title"], result["summary"], result["body"])
        time.sleep(2)
        subprocess.run([sys.executable, os.path.join(LEDGER, "fetch_articles.py")],
                       capture_output=True, timeout=300)
        arts = json.load(open(ARTICLES_FILE, encoding="utf-8")).get("articles", [])
        entry = opt._score_article(aid, arts, live=opt._load_article(aid))
        opt.record_review(aid, entry, old=old, kind=f"自动优化第{round_no}轮")
        rounds_log.append({
            "round": round_no, "title": result["title"],
            "score": entry["score"], "grade": entry["grade"],
        })
        log(f"  第{round_no}轮后评分: {entry['score']} 分（{entry['grade']}）")
        if entry["score"] >= PASS_SCORE:
            log(f"  ✓ 达标（≥{PASS_SCORE}分），停止优化")
            return True
        article = opt._load_article(aid)
    log(f"  ✗ {MAX_ROUNDS} 轮后仍未达标，停止")
    return False


def main():
    try:
        subprocess.run([sys.executable, os.path.join(LEDGER, "auto_review.py")],
                       capture_output=True, timeout=120)
    except Exception:
        pass
    cands = candidates()
    if not cands:
        log("无不合格 bot 文章（评分均 ≥60），跳过")
        return 0
    log(f"发现 {len(cands)} 篇不合格文章待自动优化：")
    for aid, meta, rv in cands:
        log(f"  - {aid} {rv.get('score')}分 {meta.get('title','')[:24]}")
    done, ok = 0, 0
    for aid, meta, rv in cands:
        log(f"开始优化文章 {aid}")
        rounds_log = []
        try:
            passed = optimize_article(aid, meta, rounds_log)
            if passed:
                ok += 1
        except Exception as e:
            log(f"文章 {aid} 自动优化失败: {str(e)[:200]}")
        done += 1
    log(f"自动优化完成：{done} 篇，达标 {ok} 篇")
    try:
        subprocess.run([sys.executable, os.path.join(LEDGER, "review_engine.py")],
                       capture_output=True, timeout=60)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
