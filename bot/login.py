"""重新登录平台获取新 JWT。

用法：python3 login.py
- 风控免 MFA 时直接密码登录；否则点 Login 触发短信，轮询 /tmp/mfa_code.txt 等待验证码（最多 10 分钟）
- 成功后写入 token.txt 并刷新 /tmp/login_state.json（fetch_detail_urls.py 依赖）
- 退出码：0 成功；2 等待验证码超时
"""
import json
import os
import time

from playwright.sync_api import sync_playwright

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(BOT_DIR, "token.txt")
LOGIN_STATE = "/tmp/login_state.json"
CODE_FILE = "/tmp/mfa_code.txt"


def _load_credentials():
    acc = os.environ.get("OPENLAB_ACCOUNT")
    pwd = os.environ.get("OPENLAB_PASSWORD")
    if acc and pwd:
        return acc, pwd
    cred_file = os.path.join(BOT_DIR, "credentials.json")
    with open(cred_file, encoding="utf-8") as f:
        d = json.load(f)
    return d.get("account"), d.get("password")


def save_token(tok):
    open(TOKEN_FILE, "w").write(tok)
    state = {"cookies": [{"name": "S-User-Token", "value": tok, "domain": ".cosmoplat.com",
                          "path": "/", "expires": time.time() + 7 * 86400,
                          "httpOnly": True, "secure": True, "sameSite": "Lax"}], "origins": []}
    with open(LOGIN_STATE, "w") as f:
        json.dump(state, f)


def main():
    acc, pwd = _load_credentials()
    if os.path.exists(CODE_FILE):
        os.remove(CODE_FILE)
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = b.new_context(viewport={"width": 1440, "height": 900})
        pg = ctx.new_page()
        pg.goto("https://openlab.cosmoplat.com/write-article", timeout=40000)
        pg.wait_for_timeout(3000)
        if "iam.cosmoplat" not in pg.url:
            pg.click(".register-btn")
            pg.wait_for_url("**iam.cosmoplat.com**", timeout=30000)
        pg.wait_for_timeout(2500)
        tab = pg.query_selector("text=Password")
        if tab:
            tab.click()
            pg.wait_for_timeout(1500)
        pg.fill("input[placeholder='Username or Email or Mobile']", acc)
        pg.fill("input[placeholder='Password']", pwd)
        for cb in pg.query_selector_all('input[type="checkbox"]'):
            try:
                if not cb.is_checked():
                    cb.check(force=True)
            except Exception:
                pass
        pg.wait_for_timeout(500)
        mfa = pg.query_selector("input[placeholder='Mfa']")
        print("MFA框(首次):", bool(mfa), flush=True)
        if mfa:
            pg.click('button:has-text("Login")')
            pg.wait_for_timeout(4000)
            print("SMS_TRIGGERED 等待验证码 -> %s" % CODE_FILE, flush=True)
            deadline = time.time() + 600
            code = None
            while time.time() < deadline:
                if os.path.exists(CODE_FILE):
                    code = open(CODE_FILE).read().strip()
                    if code:
                        break
                time.sleep(2)
            if not code:
                print("TIMEOUT: 10分钟未收到验证码", flush=True)
                b.close()
                raise SystemExit(2)
            pg.query_selector("input[placeholder='Mfa']").fill(code)
            pg.wait_for_timeout(500)
            pg.click('button:has-text("Login")')
        else:
            pg.click('button:has-text("Login")')
        st = []
        for _ in range(60):
            pg.wait_for_timeout(2000)
            st = [c for c in ctx.cookies() if c["name"] == "S-User-Token"]
            if st:
                break
        if st:
            save_token(st[-1]["value"])
            print("SUCCESS token已保存 前12位=%s" % st[-1]["value"][:12], flush=True)
        else:
            print("FAIL url=%s 页面=%s" % (pg.url[:80], pg.inner_text("body")[:120].replace("\n", " | ")), flush=True)
            raise SystemExit(1)
        b.close()


if __name__ == "__main__":
    main()
