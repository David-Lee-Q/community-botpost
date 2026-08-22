import json
import os
import sys
import subprocess
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sensitive  # noqa: E402

BASE = "https://openlab.cosmoplat.com/api"
BOT_DIR = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(os.path.dirname(BOT_DIR), "ledger")
TOKEN_FILE = os.path.join(BOT_DIR, "token.txt")

CATEGORY_IDS = {
    "机器视觉": 15, "智能制造": 1, "云计算": 2, "云原生": 3,
    "物联网": 4, "边缘计算": 5, "人工智能": 6, "大数据": 7,
    "区块链": 8, "标识解析": 9, "中间件": 10, "微服务": 11,
    "安全": 12, "编程与开发": 13, "网络": 14,
    "工业操作系统": -1, "数据要素": -2,
}


def load_token():
    if os.path.exists(TOKEN_FILE):
        return open(TOKEN_FILE).read().strip()
    return None


def save_token(token):
    open(TOKEN_FILE, "w").write(token)


def _load_credentials():
    import os as _os
    acc = _os.environ.get("OPENLAB_ACCOUNT")
    pwd = _os.environ.get("OPENLAB_PASSWORD")
    if acc and pwd:
        return acc, pwd
    cred_file = os.path.join(BOT_DIR, "credentials.json")
    if _os.path.exists(cred_file):
        with open(cred_file, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("account"), d.get("password")
    raise RuntimeError("未配置凭据：请设置 OPENLAB_ACCOUNT/OPENLAB_PASSWORD 环境变量，或创建 bot/credentials.json")


def refresh_token():
    acc, pwd = _load_credentials()
    script = '''
from playwright.sync_api import sync_playwright
import os
ACC = os.environ["ACC"]
PWD = os.environ["PWD"]
with sync_playwright() as p:
    b = p.chromium.launch(headless=True, args=["--no-sandbox","--disable-dev-shm-usage"])
    ctx = b.new_context(viewport={"width":1440,"height":900})
    pg = ctx.new_page()
    pg.goto("https://openlab.cosmoplat.com/write-article", timeout=40000)
    pg.wait_for_timeout(3000)
    pg.click(".register-btn")
    pg.wait_for_url("**iam.cosmoplat.com**", timeout=30000)
    pg.wait_for_timeout(2500)
    pw = pg.query_selector("text=Password")
    if pw:
        pw.click(); pg.wait_for_timeout(1200)
    pg.fill('input[placeholder="Username or Email or Mobile"]', ACC)
    pg.fill('input[placeholder="Password"]', PWD)
    for cb in pg.query_selector_all('input[type="checkbox"]'):
        try:
            if not cb.is_checked(): cb.check(force=True)
        except Exception: pass
    pg.wait_for_timeout(500)
    pg.click('button:has-text("Login")')
    pg.wait_for_timeout(8000)
    toks = pg.evaluate("() => document.cookie")
    import re
    m = re.search(r"(?:^|; )S-User-Token=([^;]+)", toks)
    print("TOKEN:" + (m.group(1) if m else "NONE"))
    b.close()
'''
    r = subprocess.run(["python3", "-c", script], capture_output=True, text=True,
                       timeout=120, env={**os.environ, "ACC": acc, "PWD": pwd})
    for line in r.stdout.splitlines():
        if line.startswith("TOKEN:"):
            tok = line.split("TOKEN:", 1)[1].strip()
            if tok != "NONE":
                save_token(tok)
                return tok
    raise RuntimeError("登录获取token失败: " + r.stdout[-300:] + r.stderr[-200:])


def _token_valid(tok):
    try:
        r = requests.post(
            f"{BASE}/article/list",
            headers={"s-user-token": tok, "Content-Type": "application/json"},
            json={"pageNum": 1, "pageSize": 1},
            timeout=30,
        )
        return r.json().get("code") == 0
    except Exception:
        return False


def get_token():
    tok = load_token()
    if tok and _token_valid(tok):
        return tok
    return refresh_token()


def upload_cover(token, cover_path):
    r = requests.post(
        f"{BASE}/file/uploadFileStream?type=1",
        headers={"s-user-token": token},
        files={"file": (os.path.basename(cover_path), open(cover_path, "rb"), "image/jpeg")},
        timeout=60,
    )
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError("封面上传失败: " + str(d))
    return d["data"]["path"]


def title_exists(token, title):
    page, size = 1, 50
    for _ in range(20):
        r = requests.post(
            f"{BASE}/article/list",
            headers={"s-user-token": token, "Content-Type": "application/json"},
            json={"pageNum": page, "pageSize": size},
            timeout=30,
        )
        data = r.json().get("data", {})
        lst = data.get("list", [])
        for it in lst:
            if it.get("title") == title:
                return True, it.get("id")
        total = data.get("total", 0)
        if page * size >= total or not lst:
            break
        page += 1
    return False, None


def publish_one(token, item):
    title = item["title"]
    exists, aid = title_exists(token, title)
    if exists:
        return {"status": "exists", "article_id": aid, "title": title}

    cover_url = upload_cover(token, item["cover"])
    content = open(item["file"], encoding="utf-8").read()
    lines = content.split("\n")
    while lines and lines[0].startswith("#"):
        lines.pop(0)
    body = "\n".join(lines).strip()
    summary = item["summary"]

    hits_title = sensitive.check(title)
    hits_sum = sensitive.check(summary)
    hits_body = sensitive.check(body)
    if hits_title or hits_sum or hits_body:
        hits = sorted(set(hits_title + hits_sum + hits_body))
        sensitive.log_record({
            "source": "定时发文", "article_title": title,
            "hits": hits, "action": "LLM内容优化",
        })
        if hits_title:
            title = sensitive.optimize_text(title, hits_title, "标题")
        if hits_sum:
            summary = sensitive.optimize_text(summary, hits_sum, "摘要")
        if hits_body:
            body = sensitive.optimize_text(body, hits_body, "正文")

    payload = {
        "canReply": 0, "source": 1, "cateId": CATEGORY_IDS[item["category"]],
        "activityTopic": None, "activityType": None, "contentType": "markdown",
        "sourceLink": "", "thumbnail": cover_url, "title": title,
        "description": summary, "content": body,
        "draftId": "", "articleId": None, "id": None,
        "viewRank": 0, "sectionId": None,
    }
    r = requests.post(
        f"{BASE}/article/save",
        headers={"s-user-token": token, "Content-Type": "application/json"},
        json=payload,
        timeout=60,
    )
    d = r.json()
    if d.get("code") != 0:
        return {"status": "failed", "error": str(d), "title": title}
    return {"status": "published", "article_id": d["data"]["id"], "title": title}


def show_improvements():
    import re
    tasks = os.path.join(os.path.dirname(BOT_DIR), ".cosmocode", "quality_tasks.md")
    if not os.path.exists(tasks):
        return
    text = open(tasks, encoding="utf-8").read()
    pending = re.findall(r"- \[ \] (.+?)（建议日期：", text)
    if not pending:
        return
    print("=== 本周质量改进待办 ===")
    for t in pending:
        print("  [ ] " + t)
    print("=========================")


def _register_post(aid, title, category):
    posts_file = os.path.join(LEDGER, "data", "bot_posts.json")
    try:
        posts = json.load(open(posts_file, encoding="utf-8")) if os.path.exists(posts_file) else {}
    except (OSError, ValueError):
        posts = {}
    if str(aid) not in posts:
        posts[str(aid)] = {"title": title, "cateName": category, "createTime": "",
                           "source": "定时发文"}
        with open(posts_file, "w", encoding="utf-8") as f:
            json.dump(posts, f, ensure_ascii=False, indent=2)


def publish_now(task_id):
    """强制立即发布指定计划项（忽略计划时间）。状态非 pending 时直接跳过，避免重复发布。"""
    with open(os.path.join(BOT_DIR, "plan.json"), encoding="utf-8") as f:
        plan = json.load(f)
    item = next((it for it in plan["schedule"] if it.get("taskId") == task_id), None)
    if item is None:
        return {"ok": False, "error": "计划不存在"}
    if item.get("status") != "pending":
        return {"ok": False, "skip": True,
                "reason": "当前状态 %s，不重复发布" % item.get("status")}
    item["status"] = "publishing"
    with open(os.path.join(BOT_DIR, "plan.json"), "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    token = get_token()
    result = publish_one(token, item)
    if result["status"] in ("published", "exists"):
        item["status"] = "published"
        item["article_id"] = result.get("article_id")
        item.pop("last_error", None)
        if result["status"] == "published":
            _register_post(result["article_id"], item["title"], item.get("category", ""))
        out = {"ok": True, "status": result["status"],
               "article_id": result.get("article_id"),
               "title": item["title"], "time": item.get("time")}
    else:
        item["status"] = "pending"
        item["last_error"] = str(result.get("error"))[:300]
        out = {"ok": False, "error": item["last_error"], "title": item["title"]}
    with open(os.path.join(BOT_DIR, "plan.json"), "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    return out


def main():
    show_improvements()
    token = get_token()
    with open(os.path.join(BOT_DIR, "plan.json"), encoding="utf-8") as f:
        plan = json.load(f)
    for item in plan["schedule"]:
        if item.get("status") in ("published", "publishing"):
            continue
        if item.get("time") and item.get("time") > _now_str():
            print(f"NOT_YET {item['title']} at {item['time']}")
            continue
        result = publish_one(token, item)
        if result["status"] in ("published", "exists"):
            item["status"] = "published"
            item["article_id"] = result.get("article_id")
            print(f"OK {result['status']} id={result.get('article_id')} {item['title']}")
            if result["status"] == "published":
                _register_post(result["article_id"], item["title"], item.get("category", ""))
        else:
            print(f"FAIL {item['title']}: {result.get('error')}")
    with open(os.path.join(BOT_DIR, "plan.json"), "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)


def _now_str():
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--now":
        task_id = sys.argv[2] if len(sys.argv) > 2 else ""
        if not task_id:
            print(json.dumps({"ok": False, "error": "缺少 taskId"}, ensure_ascii=False))
            sys.exit(1)
        print(json.dumps(publish_now(task_id), ensure_ascii=False))
        sys.exit(0)
    sys.exit(main())
