import json
import os
import tempfile
from playwright.sync_api import sync_playwright

PLAN_FILE = "/workspace/bot/plan.json"
USER_URL = "https://openlab.cosmoplat.com/usercenter/1447/article/全部文章"
DETAIL_PATTERN = "https://openlab.cosmoplat.com/article-detils?id={aid}&articleType=0"

plan = json.load(open(PLAN_FILE, encoding="utf-8"))

with sync_playwright() as p:
    b = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
    ctx = b.new_context(storage_state="/tmp/login_state.json", viewport={"width": 1440, "height": 900})
    pg = ctx.new_page()

    pg.goto(USER_URL, timeout=40000)
    pg.wait_for_timeout(4500)

    for item in plan["schedule"]:
        title = item["title"]
        aid = item.get("article_id")
        if not aid:
            continue
        # 以 bot 发布的 article_id 为准，pattern 确定可直出
        url = DETAIL_PATTERN.format(aid=aid)
        # 尽力从列表页点击拿真实 URL（用户指定方式），失败不影响兜底 URL
        try:
            el = pg.query_selector(f'text={title}')
            if el:
                with ctx.expect_page(timeout=8000) as popup_info:
                    el.click()
                pop = popup_info.value
                try:
                    pop.wait_for_load_state("domcontentloaded", timeout=12000)
                except Exception:
                    pass
                clicked_url = pop.url
                pop.close()
                if f"id={aid}" in clicked_url:
                    url = clicked_url
                pg.wait_for_timeout(800)
        except Exception as e:
            print(f"CLICK_FAIL {title[:24]}: {str(e)[:100]}")

        item["detail_url"] = url
        print(f"DETAIL aid={aid} -> {url}  {title[:28]}")

    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(PLAN_FILE), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PLAN_FILE)
    b.close()
