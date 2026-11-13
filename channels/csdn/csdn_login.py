"""CSDN 登录助手（人工验证码 / 人工短信码，持久化登录态）。

用法：
  python3 csdn_login.py --login --wait-code 600   人工回填验证码与短信码
  python3 csdn_login.py --check                   校验登录态是否有效
  python3 csdn_login.py --list-columns            拉取专栏列表

人工交互文件：
  channels/csdn/logs/captcha.png      风控图片验证码截图（如有）
  channels/csdn/captcha_code.txt      图片验证码，人工写入
  channels/csdn/sms_code.txt          短信验证码，人工写入
"""
import argparse
import json
import os
import time

BASE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE, "storage_state.json")
CRED_FILE = os.path.join(BASE, "credentials.json")
CONFIG_FILE = os.path.join(BASE, "config.json")
LOGS = os.path.join(BASE, "logs")
CAPTCHA_IMG = os.path.join(LOGS, "captcha.png")
CAPTCHA_CODE = os.path.join(BASE, "captcha_code.txt")
SMS_CODE = os.path.join(BASE, "sms_code.txt")
QR_IMG = os.path.join(LOGS, "qr.png")
LOGIN_URL = "https://passport.csdn.net/login"
EDITOR_URL = "https://editor.csdn.net/md/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def log(msg):
    os.makedirs(LOGS, exist_ok=True)
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    with open(os.path.join(LOGS, "csdn.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def wait_file(path, started, timeout):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if os.path.getmtime(path) >= started:
                with open(path, encoding="utf-8") as f:
                    v = f.read().strip()
                if v:
                    return v
        except OSError:
            pass
        time.sleep(1)
    raise TimeoutError("等待人工输入超时: " + os.path.basename(path))


def _launch(p):
    import os as _os
    kw = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
    px = _os.environ.get("HTTPS_PROXY") or _os.environ.get("https_proxy")
    if px:
        kw["proxy"] = {"server": px}
    return p.chromium.launch(**kw)


def _new_context(browser, use_state=True):
    kw = {"user_agent": UA, "viewport": {"width": 1440, "height": 900}}
    if use_state and os.path.exists(STATE_FILE):
        kw["storage_state"] = STATE_FILE
    return browser.new_context(**kw)


def _has_token(ctx):
    return any("usertoken" in (c.get("name") or "").lower() for c in ctx.cookies())


def cmd_check():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = _launch(p)
        ctx = _new_context(b)
        pg = ctx.new_page()
        pg.goto(EDITOR_URL, wait_until="commit", timeout=45000)
        pg.wait_for_timeout(4000)
        ok = _has_token(ctx) and "login" not in pg.url
        print(json.dumps({"ok": ok, "url": pg.url,
                          "cookies": [c.get("name") for c in ctx.cookies()][:20]},
                         ensure_ascii=False))
        b.close()
    return 0 if ok else 2


def _layer(page):
    for sel in ["#layui-layer-shade1", ".layui-layer", ".layui-layer-content"]:
        el = page.query_selector(sel)
        if el and el.is_visible():
            return el
    return None


def _handle_layer(page, wait_seconds, started, tag):
    el = _layer(page)
    if not el:
        return False
    text = ""
    try:
        text = (el.inner_text() or "").strip()
    except Exception:
        pass
    try:
        html = el.evaluate("e => e.outerHTML.slice(0,600)")
        log("[%s] 浮层DOM: %s" % (tag, " ".join(html.split())[:600]))
    except Exception:
        pass
    log("[%s] 检测到风控/提示浮层: %s" % (tag, " ".join(text.split())[:200]))
    shot = os.path.join(LOGS, "challenge_%s.png" % tag)
    try:
        el.screenshot(path=shot)
    except Exception:
        page.screenshot(path=shot, full_page=False)
    if any(k in text for k in ("滑动", "拖动", "滑块", "按住")):
        raise RuntimeError("风控要求滑块验证，无法自动完成，已截图 " + shot)
    inputs = el.query_selector_all("input[type='text'], input:not([type])")
    visible = [i for i in inputs if i.is_visible()]
    if visible:
        log("[%s] 浮层含图片验证码，截图 %s，等待人工回填 captcha_code.txt" % (tag, shot))
        code = wait_file(CAPTCHA_CODE, started, wait_seconds)
        visible[0].fill(code)
        for label in ("确定", "确认", "提交", "验证"):
            loc = page.get_by_text(label, exact=True)
            if loc.count():
                loc.first.click(timeout=6000)
                break
        page.wait_for_timeout(2500)
        return True
    for label in ("关闭", "我知道了", "知道了"):
        loc = page.get_by_text(label, exact=True)
        if loc.count():
            loc.first.click(timeout=5000)
            page.wait_for_timeout(1000)
            return True
    log("[%s] 浮层无需输入，继续" % tag)
    return True


POINT_FILE = os.path.join(BASE, "captcha_point.txt")


def _solve_click_captcha(page, wait_seconds, tag, tries=3):
    for attempt in range(1, tries + 1):
        layer = page.query_selector(".layui-layer")
        if not layer:
            return False
        panel = layer.query_selector(".verify-img-panel")
        if not panel:
            return False
        main_img = os.path.join(LOGS, "captcha_%s_%d_main.png" % (tag, attempt))
        target_img = os.path.join(LOGS, "captcha_%s_%d_target.png" % (tag, attempt))
        try:
            panel.screenshot(path=main_img)
        except Exception:
            page.screenshot(path=main_img, full_page=False)
        te = layer.query_selector(".target-image")
        if te:
            try:
                te.screenshot(path=target_img)
            except Exception:
                pass
        attempt_started = time.time()
        log("点击验证第 %d 次：主图 %s，目标图 %s，等待 captcha_point.txt 输入 x,y"
            % (attempt, main_img, target_img))
        val = wait_file(POINT_FILE, attempt_started, wait_seconds)
        try:
            x, y = [int(round(float(v))) for v in val.replace("，", ",").split(",")[:2]]
        except ValueError:
            raise RuntimeError("captcha_point.txt 格式应为 x,y")
        box = panel.bounding_box()
        page.mouse.click(box["x"] + x, box["y"] + y)
        page.wait_for_timeout(2500)
        if page.query_selector(".layui-layer") is None:
            log("点击验证通过")
            return True
        log("点击验证未通过，刷新后重试")
        try:
            page.query_selector(".verify-refresh").click()
            page.wait_for_timeout(1800)
        except Exception:
            pass
    raise RuntimeError("点击验证连续失败，截图见 logs/captcha_%s_*.png" % tag)


def _click_any(page, texts, timeout=10000):
    for t in texts:
        loc = page.get_by_text(t, exact=True)
        if loc.count():
            try:
                loc.first.click(timeout=timeout)
                return t
            except Exception:
                try:
                    loc.first.click(timeout=timeout, force=True)
                    return t
                except Exception:
                    continue
    return ""


def cmd_login(wait_seconds):
    from playwright.sync_api import sync_playwright
    cfg = _load_json(CONFIG_FILE, {})
    cred = _load_json(CRED_FILE, {})
    phone = cred.get("phone") or cfg.get("login_phone") or ""
    if not phone:
        raise SystemExit("缺少手机号：请写 channels/csdn/credentials.json 或 config.json login_phone")

    os.makedirs(LOGS, exist_ok=True)
    started = time.time()
    with sync_playwright() as p:
        b = _launch(p)
        ctx = _new_context(b, use_state=False)
        pg = ctx.new_page()
        pg.goto(LOGIN_URL, wait_until="commit", timeout=45000)
        pg.wait_for_timeout(3500)

        pg.get_by_text("验证码登录", exact=True).first.click(timeout=10000)
        pg.wait_for_timeout(1200)
        pg.get_by_placeholder("手机号").fill(phone)
        log("已填写手机号，点击获取验证码（会发送短信）")
        pg.get_by_text("获取验证码", exact=True).first.click(timeout=10000)
        pg.wait_for_timeout(3000)
        pg.screenshot(path=CAPTCHA_IMG, full_page=True, timeout=20000)
        _handle_layer(pg, wait_seconds, started, "sendcode")
        _solve_click_captcha(pg, wait_seconds, "sendcode")

        sms = wait_file(SMS_CODE, started, wait_seconds)
        pg.get_by_placeholder("6位数字验证码").fill(sms)
        pg.wait_for_timeout(1500)
        _handle_layer(pg, wait_seconds, started, "beforesubmit")
        _solve_click_captcha(pg, wait_seconds, "beforesubmit")
        clicked = _click_any(pg, ["登录/注册", "登录", "注册"])
        log("提交登录: " + (clicked or "未找到按钮"))
        pg.wait_for_timeout(4000)
        try:
            _handle_layer(pg, wait_seconds, started, "aftersubmit")
            _solve_click_captcha(pg, wait_seconds, "aftersubmit")
        except RuntimeError as e:
            log(str(e))
        pg.wait_for_timeout(2000)

        if not _has_token(ctx):
            pg.screenshot(path=os.path.join(LOGS, "login_failed.png"), full_page=True, timeout=20000)
            log("登录失败：未拿到登录态，截图 logs/login_failed.png")
            b.close()
            return 3

        ctx.storage_state(path=STATE_FILE)
        os.chmod(STATE_FILE, 0o600)
        log("登录成功，登录态已保存 " + STATE_FILE)
        b.close()
    return 0


def cmd_qr(wait_seconds=300, kind="wechat"):
    from playwright.sync_api import sync_playwright
    os.makedirs(LOGS, exist_ok=True)
    with sync_playwright() as p:
        b = _launch(p)
        ctx = _new_context(b)
        pg = ctx.new_page()
        pg.goto(LOGIN_URL, wait_until="commit", timeout=45000)
        pg.wait_for_timeout(3500)
        if kind == "app":
            for label in ("APP登录", "APP 登录", "APP扫码登录"):
                loc = pg.get_by_text(label, exact=True)
                if loc.count():
                    try:
                        loc.first.click(timeout=6000)
                        log("已切换到 " + label)
                        break
                    except Exception:
                        pass
        else:
            loc = pg.get_by_text("微信登录", exact=True)
            if loc.count():
                try:
                    loc.first.click(timeout=6000)
                    log("已切换到 微信登录")
                except Exception:
                    pass
        pg.wait_for_timeout(2500)
        shot_ok = False
        for sel in ["img[src*='qr']", "img[src*='code']", "canvas", ".login-box", ".login-mark"]:
            el = pg.query_selector(sel)
            if el:
                try:
                    el.screenshot(path=QR_IMG)
                    shot_ok = True
                    break
                except Exception:
                    continue
        if not shot_ok:
            pg.screenshot(path=QR_IMG, full_page=False)
        pg.screenshot(path=os.path.join(LOGS, "qr_full.png"), full_page=False)
        log("二维码已保存 logs/qr.png，等待扫码（最长 %d 秒）" % wait_seconds)
        end = time.time() + wait_seconds
        while time.time() < end:
            if _has_token(ctx):
                ctx.storage_state(path=STATE_FILE)
                os.chmod(STATE_FILE, 0o600)
                log("扫码登录成功，登录态已保存")
                b.close()
                return 0
            time.sleep(3)
        log("扫码等待超时")
        b.close()
    return 4


def cmd_list_columns():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = _launch(p)
        ctx = _new_context(b)
        pg = ctx.new_page()
        pg.goto("https://mp.csdn.net/mp_blog/manage/column", wait_until="commit", timeout=45000)
        pg.wait_for_timeout(5000)
        pg.screenshot(path=os.path.join(LOGS, "columns.png"), full_page=True, timeout=20000)
        data = pg.evaluate("""() => {
          const out = [];
          document.querySelectorAll('a[href*="column"], [data-column-id]').forEach(e => {
            const id = e.getAttribute('data-column-id') ||
              (e.href || '').match(/column\\/(\\d+)/)?.[1] || '';
            const name = (e.innerText || '').trim().split('\\n')[0];
            if (name && id) out.push({id, name});
          });
          return out;
        }""")
        seen, cols = set(), []
        for c in data:
            key = (c.get("id"), c.get("name"))
            if c.get("name") and key not in seen:
                seen.add(key)
                cols.append(c)
        print(json.dumps({"columns": cols, "count": len(cols)}, ensure_ascii=False, indent=2))
        b.close()
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", action="store_true")
    ap.add_argument("--qr", action="store_true")
    ap.add_argument("--qr-kind", default="wechat", choices=["wechat", "app"])
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--list-columns", action="store_true")
    ap.add_argument("--wait-code", type=int, default=600)
    args = ap.parse_args()
    if args.qr:
        return cmd_qr(args.wait_code, args.qr_kind)
    if args.check:
        return cmd_check()
    if args.list_columns:
        return cmd_list_columns()
    if args.login:
        return cmd_login(args.wait_code)
    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
