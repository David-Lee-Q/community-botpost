"""按分类批量创建 CSDN 专栏，并把 id 写回 config.json。

python3 make_columns.py [--names a,b] [--days 7] [--dry-run]
"""
import argparse, json, os, time, datetime
from playwright.sync_api import sync_playwright

BASE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(BASE, "storage_state.json")
CONFIG = os.path.join(BASE, "config.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
ADD_URL = "https://mp.csdn.net/mp_blog/manage/column/columnAdd"
LIST_URL = "https://mp.csdn.net/mp_blog/manage/column/allColumnList"


def cats_from_plan(path, days):
    d = json.load(open(path, encoding="utf-8"))
    cutoff = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    out = []
    for it in d.get("schedule", []):
        if not it.get("article_id"):
            continue
        if (it.get("time") or "")[:10] < cutoff:
            continue
        c = it.get("category")
        if c and c not in out:
            out.append(c)
    return out


def read_columns(pg):
    got = {}
    def on_resp(r):
        if "column/list" in r.url:
            try:
                j = r.json()
                for c in j["data"]["list"]["column"]:
                    got[c["title"]] = {"id": c["id"], "name": c["title"]}
            except Exception:
                pass
    pg.on("response", on_resp)
    pg.goto(LIST_URL, wait_until="commit", timeout=60000)
    pg.wait_for_timeout(5000)
    pg.remove_listener("response", on_resp)
    return got


def create_one(pg, name):
    pg.goto(ADD_URL, wait_until="commit", timeout=60000)
    pg.wait_for_selector("input[placeholder='请输入分类专栏名称']", timeout=25000)
    inp = pg.get_by_placeholder("请输入分类专栏名称").first
    inp.click()
    inp.fill("")
    inp.type(name, delay=20)
    ta = pg.query_selector("textarea")
    if not ta:
        return "no-textarea"
    ta.click()
    ta.fill("%s领域的产业观察与技术实践。" % name)
    pg.wait_for_timeout(300)
    if not (ta.input_value() or "").strip():
        ta.type("%s领域的产业观察与技术实践。" % name, delay=10)
    pg.get_by_text("提交", exact=True).first.click(timeout=10000)
    pg.wait_for_timeout(3000)
    msgs = pg.evaluate("""()=>[...document.querySelectorAll('.el-message,.el-message__content,.el-form-item__error,[class*=message]')]
        .map(e=>(e.innerText||'').trim()).filter(Boolean).slice(0,5)""")
    return {"url": pg.url, "msgs": msgs}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-plan", default="/workspace/bot/plan.json")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--names", default="")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    wanted = [s.strip() for s in a.names.split(",") if s.strip()] or cats_from_plan(a.from_plan, a.days)
    if a.dry_run:
        print("待建:", wanted)
        return
    with sync_playwright() as p:
        kw = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
        px = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        if px:
            kw["proxy"] = {"server": px}
        b = p.chromium.launch(**kw)
        ctx = b.new_context(user_agent=UA, viewport={"width": 1440, "height": 900},
                            storage_state=STATE)
        pg = ctx.new_page()
        cols = read_columns(pg)
        print("已有:", list(cols.keys()))
        for name in wanted:
            if name in cols:
                print("skip", name)
                continue
            try:
                r = create_one(pg, name)
                print("create", name, json.dumps(r, ensure_ascii=False)[:200])
            except Exception as e:
                print("create", name, "ERR", e)
            time.sleep(1.5)
        cols = read_columns(pg)
        print("最终:", json.dumps(cols, ensure_ascii=False))
        b.close()
    cfg = json.load(open(CONFIG, encoding="utf-8")) if os.path.exists(CONFIG) else {}
    cfg["columns"] = cols
    json.dump(cfg, open(CONFIG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("config.columns ->", len(cols))


if __name__ == "__main__":
    main()
