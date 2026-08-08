import json
from playwright.sync_api import sync_playwright

PLAN_FILE = "/workspace/bot/plan.json"
USER_URL = "https://openlab.cosmoplat.com/usercenter/1447/article/全部文章"
DETAIL_PATTERN = "https://openlab.cosmoplat.com/article-detils?id={aid}&articleType=0"

plan = json.load(open(PLAN_FILE, encoding="utf-8"))

with sync_playwright() as p:
    b = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
    ctx = b.new_context(storage_state="/tmp/login_state.json", viewport={"width": 1440, "height": 900})
    pg = ctx.new_page()

    for item in plan["schedule"]:
        title = item["title"]
        aid = item.get("article_id")
        url = None

        # 1) 从列表页点击获取真实 URL（用户指定的方式）
        try:
            pg.goto(USER_URL, timeout=40000)
            pg.wait_for_timeout(4500)
            el = pg.query_selector(f'text={title}')
            if el:
                with ctx.expect_page(timeout=10000) as popup_info:
                    el.click()
                pop = popup_info.value
                try:
                    pop.wait_for_load_state("domcontentloaded", timeout=15000)
                except Exception:
                    pass
                pg.wait_for_timeout(1500)
                clicked_url = pop.url
                pop.close()
            else:
                clicked_url = None
        except Exception as e:
            clicked_url = None
            print(f"CLICK_FAIL {title}: {str(e)[:120]}")

        # 2) 校验：以 bot 发布的 article_id 为准，构造详情 URL 并打开验证标题
        if aid:
            cand = DETAIL_PATTERN.format(aid=aid)
            if clicked_url and f"id={aid}" in clicked_url:
                url = clicked_url
            else:
                try:
                    chk = ctx.new_page()
                    chk.goto(cand, timeout=40000)
                    chk.wait_for_timeout(3500)
                    if title in chk.inner_text("body"):
                        url = cand
                    else:
                        print(f"VERIFY_FAIL {title}: {cand} 标题不匹配")
                    chk.close()
                except Exception as e:
                    print(f"VERIFY_ERR {title}: {str(e)[:120]}")

        item["detail_url"] = url
        print(f"DETAIL aid={aid} -> {url}  {title[:28]}")

    json.dump(plan, open(PLAN_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    b.close()
