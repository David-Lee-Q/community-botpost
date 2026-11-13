"""CSDN 登录中转台：浏览器跑在服务器，人工在网页上看图、点击、输码。

python3 webui.py [--port 8300]
"""
import argparse
import base64
import datetime as dt
import hashlib
import io
import json
import os
import queue
import tempfile
import threading
import time
import traceback

from flask import Flask, Response, jsonify, request

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(BASE))
STATE_FILE = os.path.join(BASE, "storage_state.json")
CRED_FILE = os.path.join(BASE, "credentials.json")
LOGS = os.path.join(BASE, "logs")
DIST_STATE = os.path.join(BASE, "state.json")
DIST_QUEUE = os.path.join(BASE, "queue.json")
DIST_CONFIG = os.path.join(BASE, "config.json")
PLAN_FILE = os.path.join(ROOT, "bot", "plan.json")
LOGIN_URL = "https://passport.csdn.net/login"
EDITOR_URL = "https://editor.csdn.net/md/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
VIEW_W, VIEW_H = 1280, 800


def _log(msg):
    os.makedirs(LOGS, exist_ok=True)
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    with open(os.path.join(LOGS, "webui.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


class Worker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.q = queue.Queue()
        self.ready = False
        self.error = ""
        self.url = ""
        self.logged_in = False
        self.saved = False
        self.last_hash = ""
        self.last_b64 = ""
        self.started = time.time()
        self.last_click = None
        self.last_points = []

    def run(self):
        from playwright.sync_api import sync_playwright
        try:
            with sync_playwright() as p:
                kw = {"headless": True,
                      "args": ["--no-sandbox", "--disable-dev-shm-usage",
                               "--disable-blink-features=AutomationControlled"]}
                px = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
                if px:
                    kw["proxy"] = {"server": px}
                self.browser = p.chromium.launch(**kw)
                self._new_ctx()
                try:
                    self.page.goto(LOGIN_URL, wait_until="commit", timeout=45000)
                    self.page.wait_for_timeout(2500)
                except Exception:
                    pass
                self.ready = True
                _log("浏览器就绪")
                while True:
                    cmd, args, ev, out = self.q.get()
                    try:
                        out["result"] = getattr(self, "do_" + cmd)(*args)
                    except Exception as e:
                        out["error"] = "%s: %s" % (type(e).__name__, e)
                        _log("命令 %s 失败 %s" % (cmd, traceback.format_exc(limit=2)))
                    ev.set()
        except Exception as e:
            self.error = "%s: %s" % (type(e).__name__, e)
            _log("worker 退出 " + traceback.format_exc())

    def _new_ctx(self, use_state=False):
        if getattr(self, "ctx", None):
            try:
                self.ctx.close()
            except Exception:
                pass
        self.saved = False
        self.last_hash = ""
        self.last_b64 = ""
        kw = {"user_agent": UA, "viewport": {"width": VIEW_W, "height": VIEW_H}}
        if use_state and os.path.exists(STATE_FILE):
            kw["storage_state"] = STATE_FILE
        self.ctx = self.browser.new_context(**kw)
        self.page = self.ctx.new_page()
        self.page.on("framenavigated", self._on_nav)
        self.url = ""

    def _on_nav(self, frame):
        if frame == self.page.main_frame:
            self.url = frame.url

    def call(self, cmd, *args, timeout=20):
        if not self.ready:
            raise RuntimeError(self.error or "浏览器启动中，请稍候")
        ev = threading.Event()
        out = {}
        self.q.put((cmd, args, ev, out))
        if not ev.wait(timeout):
            raise RuntimeError("执行超时: " + cmd)
        if "error" in out:
            raise RuntimeError(out["error"])
        return out.get("result")

    # ---- 命令 ----
    def _cookie_has_token(self):
        return any("usertoken" in (c.get("name") or "").lower()
                   for c in self.ctx.cookies())

    def _maybe_save(self):
        if self._cookie_has_token():
            self.logged_in = True
            if not self.saved:
                self.ctx.storage_state(path=STATE_FILE)
                try:
                    os.chmod(STATE_FILE, 0o600)
                except OSError:
                    pass
                self.saved = True
                _log("检测到 UserToken，登录态已保存")
        else:
            self.logged_in = False
        return self.logged_in

    def do_shot(self):
        jpg = self.page.screenshot(type="jpeg", quality=72)
        h = hashlib.md5(jpg).hexdigest()
        self._maybe_save()
        panel = self.page.evaluate("""()=>{
          const e=document.querySelector('.verify-img-panel');
          if(!e) return null;
          const r=e.getBoundingClientRect();
          return [Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)];
        }""")
        return {"b64": base64.b64encode(jpg).decode(), "hash": h,
                "url": self.page.url, "logged_in": self.logged_in,
                "saved": self.saved, "error": self.error, "panel": panel}

    def do_panel(self):
        from PIL import Image, ImageDraw
        el = self.page.query_selector(".verify-img-panel")
        if not el:
            return {"ok": False}
        box = el.bounding_box()
        png = el.screenshot(type="png")
        im = Image.open(io.BytesIO(png)).convert("RGB")
        im = im.resize((im.width * 2, im.height * 2), Image.LANCZOS)
        d = ImageDraw.Draw(im)
        for (px, py, ts) in self.last_points:
            if time.time() - ts > 15:
                continue
            cx = (px - box["x"]) * 2
            cy = (py - box["y"]) * 2
            d.ellipse([cx - 16, cy - 16, cx + 16, cy + 16], outline=(255, 0, 0), width=4)
            d.line([cx - 26, cy, cx + 26, cy], fill=(255, 0, 0), width=2)
            d.line([cx, cy - 26, cx, cy + 26], fill=(255, 0, 0), width=2)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=88)
        tgt = None
        t = self.page.query_selector(".target-image")
        if t:
            tp = t.screenshot(type="png")
            ti = Image.open(io.BytesIO(tp)).convert("RGB")
            ti = ti.resize((ti.width * 2, ti.height * 2), Image.LANCZOS)
            tb = io.BytesIO()
            ti.save(tb, "JPEG", quality=88)
            tgt = base64.b64encode(tb.getvalue()).decode()
        return {"ok": True, "b64": base64.b64encode(buf.getvalue()).decode(),
                "target": tgt, "box": [box["x"], box["y"], box["width"], box["height"]],
                "scale": 2}

    def do_click(self, x, y):
        info = self.page.evaluate("""([x,y])=>{
          const e=document.elementFromPoint(x,y);
          const out={tag:'',cls:'',id:'',box:null};
          if(e){const r=e.getBoundingClientRect();
            out.tag=e.tagName; out.cls=(e.className||'').toString().slice(0,120);
            out.id=e.id||''; out.box=[Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)];}
          const frames=[...document.querySelectorAll('iframe')].map(f=>{
            const r=f.getBoundingClientRect();
            return {src:(f.src||'').slice(0,120), box:[Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)]};});
          const layer=document.querySelector('.layui-layer');
          return {hit:out, frames:frames, layer:!!layer,
                  layerBox: layer? (()=>{const r=layer.getBoundingClientRect();return [Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)];})():null};
        }""", [float(x), float(y)])
        self.last_click = (float(x), float(y), time.time())
        self.last_points = [(float(x), float(y), time.time())]
        self.page.mouse.move(float(x), float(y))
        self.page.wait_for_timeout(150)
        self.page.mouse.down()
        self.page.wait_for_timeout(80)
        self.page.mouse.up()
        self.page.wait_for_timeout(900)
        info["url"] = self.page.url
        _log("click %s,%s hit=%s layer=%s" % (x, y, info.get("hit"), info.get("layer")))
        return info

    def do_click_multi(self, points):
        pts = [(float(p[0]), float(p[1])) for p in (points or []) if len(p) >= 2][:8]
        sent = 0
        for (x, y) in pts:
            if not self.page.query_selector(".layui-layer"):
                break
            self.page.mouse.move(x, y)
            self.page.wait_for_timeout(120)
            self.page.mouse.down()
            self.page.wait_for_timeout(70)
            self.page.mouse.up()
            sent += 1
            self.page.wait_for_timeout(420)
        self.last_points = [(x, y, time.time()) for (x, y) in pts]
        self.page.wait_for_timeout(500)
        still = bool(self.page.query_selector(".layui-layer"))
        _log("click_multi %s sent=%s layer=%s" % (pts, sent, still))
        return {"sent": sent, "layer": still,
                "text": self.page.evaluate("""()=>{const l=document.querySelector('.layui-layer');return l?(l.innerText||'').replace(/\s+/g,' ').slice(0,80):'';}""")}

    def do_captcha_info(self):
        return self.page.evaluate("""()=>{
          const layer=document.querySelector('.layui-layer');
          const ifr=[...document.querySelectorAll('iframe')].map(f=>{
            const r=f.getBoundingClientRect();
            return {src:(f.src||'').slice(0,160), box:[Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)]};});
          if(!layer) return {layer:false, iframes:ifr, url:location.href};
          const r=layer.getBoundingClientRect();
          const imgs=[...layer.querySelectorAll('img')].map(i=>{
            const b=i.getBoundingClientRect();
            return {cls:(i.className||'').toString().slice(0,80), w:Math.round(b.width), h:Math.round(b.height), box:[Math.round(b.x),Math.round(b.y),Math.round(b.width),Math.round(b.height)]};
          });
          return {layer:true, text:(layer.innerText||'').replace(/\s+/g,' ').slice(0,300),
                  box:[Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)],
                  imgs:imgs, iframes:ifr, url:location.href};
        }""")

    def do_dom(self):
        return self.page.evaluate("""()=>{
          const layer=document.querySelector('.layui-layer');
          const canv=[...document.querySelectorAll('canvas')].map(c=>{
            const r=c.getBoundingClientRect();
            return [Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)];});
          const sh=[...document.querySelectorAll('*')].filter(e=>e.shadowRoot).length;
          const bg=[...document.querySelectorAll('.layui-layer *')].filter(e=>{
            const s=getComputedStyle(e); return s.backgroundImage && s.backgroundImage!=='none';
          }).slice(0,10).map(e=>{const r=e.getBoundingClientRect();
            return {cls:(e.className||'').toString().slice(0,60), bg:s_bg(e), box:[Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)]};});
          function s_bg(e){const s=getComputedStyle(e).backgroundImage;return s.slice(0,120);}
          return {html: layer? layer.outerHTML.slice(0,4000):'', canvases:canv, shadowHosts:sh, bgImages:bg, url:location.href};
        }""")

    def do_refresh_captcha(self):
        for sel in [".layui-layer .verify-refresh", ".verify-refresh", ".layui-layer img[src*='refresh']"]:
            el = self.page.query_selector(sel)
            if el:
                try:
                    el.click(timeout=5000)
                    self.page.wait_for_timeout(1200)
                    return {"ok": True, "how": sel}
                except Exception:
                    pass
        for label in ("换一张", "点击刷新", "刷新"):
            loc = self.page.get_by_text(label, exact=True)
            if loc.count():
                try:
                    loc.first.click(timeout=5000)
                    self.page.wait_for_timeout(1200)
                    return {"ok": True, "how": label}
                except Exception:
                    pass
        return {"ok": False}

    def do_type(self, text):
        self.page.keyboard.insert_text(text)
        return {"ok": True}

    def do_sms(self, code, submit=True):
        code = str(code or "").strip()
        el = None
        for sel in ["input[placeholder*='6位']", "input[placeholder*='验证码']",
                    "input[name='code']", "input[maxlength='6']",
                    "input[type='text']"]:
            try:
                e = self.page.query_selector(sel)
                if e and e.is_visible():
                    el = e
                    break
            except Exception:
                pass
        if not el:
            return {"ok": False, "reason": "no_input", "url": self.page.url}
        el.click()
        self.page.wait_for_timeout(200)
        el.fill(code)
        self.page.wait_for_timeout(250)
        val = el.input_value()
        clicked = None
        if submit:
            for label in ("登录", "立即登录", "登 录"):
                loc = self.page.get_by_text(label, exact=True)
                if loc.count():
                    try:
                        loc.first.click(timeout=4000)
                        clicked = label
                        break
                    except Exception:
                        pass
            if not clicked:
                el.press("Enter")
        self.page.wait_for_timeout(2500)
        self._maybe_save()
        _log("sms filled len=%s clicked=%s url=%s" % (len(code), clicked, self.page.url))
        return {"ok": True, "value": val, "clicked": clicked,
                "url": self.page.url, "logged_in": self.logged_in}

    def do_key(self, key):
        self.page.keyboard.press(key)
        return {"ok": True}

    def do_reload(self):
        self.page.reload(wait_until="commit", timeout=45000)
        self.page.wait_for_timeout(1500)
        return {"url": self.page.url}

    def do_login(self):
        self.page.goto(LOGIN_URL, wait_until="commit", timeout=45000)
        self.page.wait_for_timeout(2500)
        return {"url": self.page.url}

    def do_sendcode(self):
        cred = {}
        try:
            with open(CRED_FILE, encoding="utf-8") as f:
                cred = json.load(f)
        except (OSError, ValueError):
            pass
        phone = cred.get("phone", "")
        self.page.goto(LOGIN_URL, wait_until="commit", timeout=45000)
        self.page.wait_for_timeout(2500)
        try:
            self.page.get_by_text("验证码登录", exact=True).first.click(timeout=8000)
        except Exception:
            pass
        self.page.wait_for_timeout(800)
        filled = False
        for sel in ["input[placeholder*='手机号']", "input[placeholder*='手机']",
                    "input[name='phone']", "input[type='tel']"]:
            try:
                el = self.page.query_selector(sel)
                if el and el.is_visible():
                    el.fill(phone)
                    filled = True
                    break
            except Exception:
                pass
        for label in ("获取验证码", "发送验证码"):
            try:
                self.page.get_by_text(label, exact=True).first.click(timeout=6000)
                break
            except Exception:
                pass
        self.page.wait_for_timeout(1200)
        return {"url": self.page.url, "phone_filled": filled, "phone": phone}

    def do_restart(self):
        self._new_ctx(use_state=False)
        self.page.goto(LOGIN_URL, wait_until="commit", timeout=45000)
        self.page.wait_for_timeout(2500)
        _log("会话已重启")
        return {"url": self.page.url}

    def do_editor(self):
        self.page.goto(EDITOR_URL, wait_until="commit", timeout=45000)
        self.page.wait_for_timeout(3000)
        self._maybe_save()
        return {"url": self.page.url, "logged_in": self.logged_in}

    def do_save(self):
        ok = self._maybe_save()
        if ok:
            self.ctx.storage_state(path=STATE_FILE)
            os.chmod(STATE_FILE, 0o600)
            self.saved = True
        return {"logged_in": ok, "saved": self.saved}

    def do_status(self):
        self._maybe_save()
        return {"ready": self.ready, "url": self.page.url,
                "logged_in": self.logged_in, "saved": self.saved,
                "error": self.error}


# ---------- 一键分发 ----------

def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _runner_alive():
    try:
        pid = int(open(os.path.join(LOGS, "runner.pid")).read().strip())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def _login_ok():
    st = _load_json(STATE_FILE, {})
    return any("usertoken" in (c.get("name") or "").lower()
               for c in st.get("cookies", []))


def _plan_items(days):
    plan = _load_json(PLAN_FILE, {})
    cutoff = (dt.datetime.now() - dt.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    items = [it for it in plan.get("schedule", [])
             if it.get("status") == "published" and it.get("article_id")
             and (it.get("time") or "") >= cutoff]
    items.sort(key=lambda x: x.get("time") or "", reverse=True)
    return items


def _merged_rows(days):
    st = _load_json(DIST_STATE, {})
    q = _load_json(DIST_QUEUE, {"jobs": []})
    articles = st.get("articles", {})
    jobs = {j["article_id"]: j for j in q.get("jobs", [])}
    rows = []
    for it in _plan_items(days):
        aid = str(it["article_id"])
        a = articles.get(aid) or {}
        j = jobs.get(aid)
        if a.get("status") == "success":
            status = "published"
        elif a.get("status") == "failed":
            status = "failed"
        elif j:
            status = "queued"
        else:
            status = "pending"
        rows.append({
            "article_id": aid,
            "title": it.get("title", ""),
            "time": it.get("time", ""),
            "status": status,
            "url": a.get("url", ""),
            "error": a.get("error", ""),
            "attempts": (j or a or {}).get("attempts", 0),
            "priority": bool(j and j.get("priority")),
            "file": it.get("file", ""),
            "cover": (it.get("body_image_files") or [it.get("cover", "")])[0] or it.get("cover", ""),
            "category": it.get("category", ""),
        })
    return rows


def _job_from_row(row):
    imgs = []
    rowf = row.get("file", "")
    if rowf and os.path.exists(rowf):
        try:
            txt = open(rowf, encoding="utf-8").read()
            import re
            imgs = [m for m in re.findall(r"!\[[^\]]*\]\(([^)\s]+)\)", txt)]
        except OSError:
            pass
    if not imgs and row.get("cover"):
        imgs = [row["cover"]]
    return {
        "article_id": row["article_id"],
        "title": row.get("title", ""),
        "file": rowf,
        "cover": imgs[0] if imgs else row.get("cover", ""),
        "body_image_files": imgs,
        "category": row.get("category", ""),
        "time": row.get("time", ""),
        "attempts": 0,
        "next_try_at": "",
        "backfill": (row.get("time") or "") < dt.date.today().strftime("%Y-%m-%d") + " 00:00:00",
        "priority": True,
        "manual": True,
    }


def _overview():
    st = _load_json(DIST_STATE, {})
    q = _load_json(DIST_QUEUE, {"jobs": []})
    cfg = _load_json(DIST_CONFIG, {})
    today = dt.date.today().strftime("%Y-%m-%d")
    day = (st.get("days") or {}).get(today, {})
    ov = {
        "login_ok": _login_ok(),
        "runner_alive": _runner_alive(),
        "needs_login": bool((st.get("session") or {}).get("needs_login")),
        "quota_blocked_today": st.get("quota_blocked_date") == today,
        "today_total": day.get("total", 0),
        "daily_limit": int(cfg.get("daily_limit", 2)),
        "today_backfill": day.get("backfill", 0),
        "backfill_limit": int(cfg.get("backfill_daily_limit", 2)),
        "queued": len(q.get("jobs", [])),
        "counters": st.get("counters", {}),
        "window": cfg.get("publish_window", ["08:00", "20:00"]),
    }
    try:
        import csdn_api
        r = csdn_api.article_list(csdn_api.BizAPI())
        ov["csdn_articles"] = (r.get("data") or {}).get("count", {}).get("publish", 0)
        ov["api_ok"] = True
    except Exception:
        ov["api_ok"] = False
    return ov


app = Flask(__name__)
worker = Worker()

INDEX = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CSDN 登录中转台</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#f4f5f7;font:14px/1.6 -apple-system,"Segoe UI","PingFang SC",sans-serif;color:#222}
header{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:10px 14px;background:#fff;border-bottom:1px solid #e3e5e8;position:sticky;top:0;z-index:9}
h1{font-size:15px;margin:0 12px 0 0}
button{padding:6px 12px;border:1px solid #c9ced6;background:#fff;border-radius:6px;cursor:pointer}
button:hover{background:#f0f2f5}
button.primary{background:#c92027;border-color:#c92027;color:#fff}
button.primary:hover{background:#ad1b21}
.badge{padding:2px 8px;border-radius:10px;font-size:12px;background:#eceff3}
.badge.on{background:#e3f5e9;color:#1a7f37}
.badge.off{background:#fdeaea;color:#b42318}
main{display:flex;gap:14px;padding:14px;align-items:flex-start}
#wrap{flex:1;min-width:0;background:#fff;border:1px solid #e3e5e8;border-radius:8px;padding:8px;overflow:auto;max-height:calc(100vh - 130px)}
#stage{position:relative;display:inline-block}
#shot{display:block;max-width:none;cursor:crosshair;user-select:none}
#mark{position:absolute;width:14px;height:14px;margin:-7px 0 0 -7px;border:2px solid #c92027;border-radius:50%;box-shadow:0 0 0 2px #fff;display:none;pointer-events:none}
aside{width:290px;flex:none;background:#fff;border:1px solid #e3e5e8;border-radius:8px;padding:12px}
aside h2{font-size:13px;margin:0 0 6px;color:#555}
aside section{margin-bottom:14px;padding-bottom:12px;border-bottom:1px dashed #e3e5e8}
aside section:last-child{border:0;margin:0;padding:0}
.row{display:flex;gap:6px;flex-wrap:wrap}
input[type=text]{flex:1;min-width:100px;padding:6px 8px;border:1px solid #c9ced6;border-radius:6px}
.hint{color:#6b7280;font-size:12px;margin-top:4px}
#msg{font-size:12px;color:#1a7f37;min-height:18px}
#url{font-size:11px;color:#6b7280;word-break:break-all}
.zoom button{padding:4px 8px;font-size:12px}
</style></head><body>
<header>
  <h1>CSDN 登录中转台</h1>
  <a href="." style="color:#1a56b0;text-decoration:none;font-weight:600">一键分发</a>
  <button class="primary" onclick="act('/sendcode')">1. 填手机号并获取验证码</button>
  <button onclick="act('/login')">打开登录页</button>
  <button onclick="act('/restart')">重启浏览器</button>
  <button onclick="act('/editor')">2. 去编辑器校验登录</button>
  <span id="login" class="badge">未登录</span>
  <span id="saved" class="badge">未保存</span>
  <span id="msg"></span>
</header>
<main>
  <div id="wrap">
    <div id="panelbox" style="display:none;border-bottom:1px solid #e3e5e8;margin-bottom:10px;padding-bottom:10px">
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:6px">
        <b style="font-size:13px">验证码特写（按目标条从左到右，依次点上方大图，1-4 个）</b>
        <button onclick="loadPanel()">刷新特写</button>
      </div>
      <div id="panelwrap" style="position:relative;display:inline-block;max-width:100%">
        <img id="panel" style="display:block;max-width:100%;cursor:crosshair;border:1px solid #d5d9df;border-radius:4px">
      </div>
      <img id="target" style="display:block;margin-top:6px;max-width:100%;border:1px solid #d5d9df;border-radius:4px">
      <div class="hint" id="panelpt">未选择位置</div>
      <div class="row" style="margin-top:6px">
        <button class="primary" onclick="sendPoints()">发送点击</button>
        <button onclick="undoPoint()">撤销</button>
        <button onclick="clearPoints()">清空</button>
      </div>
    </div>
    <div id="stage">
    <img id="shot" alt="加载中">
    <div id="mark"></div>
  </div></div>
  <aside>
    <section>
      <h2>画面</h2>
      <div class="row zoom">
        <button onclick="setZoom(1)">100%</button>
        <button onclick="setZoom(1.5)">150%</button>
        <button onclick="setZoom(2)">200%</button>
        <button onclick="setZoom(3)">300%</button>
      </div>
      <div class="hint">每 2 秒自动刷新。放大后用滚动条移动。</div>
    </section>
    <section>
      <h2>点击（先定位，再发送）</h2>
      <div class="row">
        <button class="primary" onclick="sendClick()">发送点击</button>
        <button onclick="clearMark()">清除</button>
      </div>
      <div class="hint" id="pt">未选择位置</div>
      <div class="hint" id="hit"></div>
      <div class="row" style="margin-top:6px">
        <button onclick="act('/refresh_captcha')">刷新验证码</button>
        <button onclick="act('/captcha_info')">检查浮层</button>
      </div>
      <pre id="info" style="font-size:11px;color:#6b7280;white-space:pre-wrap;max-height:120px;overflow:auto;margin:6px 0 0"></pre>
    </section>
    <section>
      <h2>输入短信验证码</h2>
      <div class="row">
        <input id="txt" type="text" inputmode="numeric" placeholder="6 位数字">
        <button onclick="sendText()">输入</button>
        <button class="primary" onclick="fillSms()">填入并登录</button>
      </div>
      <div class="row" style="margin-top:6px">
        <button onclick="sendKey('Enter')">回车</button>
        <button onclick="sendKey('Backspace')">退格</button>
        <button onclick="sendKey('Tab')">Tab</button>
      </div>
      <div class="hint">先在画面里点一下验证码输入框，再点「输入」；最后按回车提交登录。</div>
    </section>
    <section>
      <h2>状态</h2>
      <div id="url"></div>
      <div class="hint">登录成功后会自动写入 storage_state.json。</div>
    </section>
  </aside>
</main>
<script>
const img=document.getElementById('shot'), mark=document.getElementById('mark');
let pending=null, zoom=1, busy=false, shown='', panelBox=null, panelOn=false, points=[];
img.onload=()=>{ img.style.width=(zoom*100)+'%'; };
function setZoom(z){ zoom=z; img.style.width=(zoom*100)+'%'; }
const panel=document.getElementById('panel');
function renderPoints(){
  document.querySelectorAll('#panelwrap .dot').forEach(n=>n.remove());
  points.forEach((p,i)=>{
    const s=document.createElement('span'); s.className='dot'; s.textContent=i+1;
    s.style.cssText='position:absolute;left:'+p.cx+'px;top:'+p.cy+'px;transform:translate(-50%,-50%);background:#c92027;color:#fff;border-radius:50%;width:20px;height:20px;line-height:20px;text-align:center;font-size:12px;pointer-events:none;box-shadow:0 0 0 2px #fff';
    document.getElementById('panelwrap').appendChild(s);
  });
  document.getElementById('panelpt').textContent = points.length? ('已选 '+points.length+' 个：'+points.map(p=>p.x+','+p.y).join('  ')) : '未选择位置';
}
panel.addEventListener('click', e=>{
  if(!panelBox) return;
  const r=panel.getBoundingClientRect();
  const k=panelBox[2]/r.width;
  points.push({x:Math.round(panelBox[0]+(e.clientX-r.left)*k), y:Math.round(panelBox[1]+(e.clientY-r.top)*k), cx:e.clientX-r.left, cy:e.clientY-r.top});
  renderPoints();
});
function undoPoint(){ points.pop(); renderPoints(); }
function clearPoints(){ points=[]; renderPoints(); }
async function sendPoints(){
  if(!points.length){ msg('先在特写上点选目标',false); return; }
  try{
    const d=await post('/click_multi',{points:points.map(p=>({x:p.x,y:p.y}))});
    msg('已发送 '+d.sent+' 次点击'+(d.layer?'，浮层仍在：'+d.text:'，浮层已关闭'));
    setTimeout(loadPanel, 1200);
  }catch(e){ msg('发送失败 '+e,false); }
}
img.addEventListener('click', e=>{
  const r=img.getBoundingClientRect();
  const sx=img.naturalWidth/r.width, sy=img.naturalHeight/r.height;
  const x=Math.round(e.clientX-r.left)*sx, y=Math.round(e.clientY-r.top)*sy;
  pending={x,y};
  mark.style.display='block';
  mark.style.left=(e.clientX-r.left)+'px';
  mark.style.top=(e.clientY-r.top)+'px';
  document.getElementById('pt').textContent='待发送坐标 '+x+', '+y;
});
function clearMark(){ pending=null; mark.style.display='none'; document.getElementById('pt').textContent='未选择位置'; }
const D=new URL('.',location.href).pathname;
async function post(url, body){
  const r=await fetch(D+url.replace(/^\//,''),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});
  return await r.json();
}
function msg(t,ok){ const m=document.getElementById('msg'); m.textContent=t||''; m.style.color=ok===false?'#b42318':'#1a7f37'; }
async function sendClick(){
  if(!pending){ msg('先在画面上点一下位置',false); return; }
  try{
    const d=await post('/click',pending);
    setTimeout(loadPanel, 800);
    msg('已点击 '+pending.x+', '+pending.y);
    const h=d.hit||{};
    document.getElementById('hit').textContent='命中: '+(h.tag||'?')+(h.cls?('.'+h.cls.split(' ')[0]):'')+(h.id?('#'+h.id):'');
    document.getElementById('info').textContent=JSON.stringify({hit:d.hit,layer:d.layer,frames:d.frames},null,1);
    clearMark(); poll(true);
  }catch(e){ msg('点击失败 '+e,false); }
}
async function sendText(){
  const t=document.getElementById('txt').value.trim();
  if(!t){ msg('先填短信码',false); return; }
  try{ await post('/type',{text:t}); msg('已输入'); poll(true); }catch(e){ msg('输入失败 '+e,false); }
}
async function fillSms(){
  const t=document.getElementById('txt').value.trim();
  if(!t){ msg('先填短信码',false); return; }
  msg('提交中...');
  try{ const d=await post('/sms',{code:t,submit:true});
    msg(d.ok? ('已填入 '+d.value+'，'+(d.clicked?'已点登录':'已回车')+'，当前 '+(d.logged_in?'已登录':'未登录')) : '未找到验证码输入框');
    poll(true);
  }catch(e){ msg('提交失败 '+e,false); }
}
async function sendKey(k){ try{ await post('/key',{key:k}); poll(true); }catch(e){ msg('按键失败 '+e,false); } }
async function loadPanel(){
  try{
    const d=await post('/panel');
    if(!d.ok){ document.getElementById('panelbox').style.display='none'; panelOn=false; return; }
    panelBox=d.box; panelOn=true; points=[]; renderPoints();
    document.getElementById('panelbox').style.display='block';
    document.getElementById('panel').src='data:image/jpeg;base64,'+d.b64;
    if(d.target) document.getElementById('target').src='data:image/jpeg;base64,'+d.target;
  }catch(e){}
}
async function act(url){
  msg('执行中...');
  try{ const d=await post(url); msg('完成');
    if(d.layer!==undefined||d.text!==undefined){ document.getElementById('info').textContent=JSON.stringify(d,null,1); }
    if(d.phone) msg('已填手机号 '+d.phone+'，请完成点击验证'); poll(true);}
  catch(e){ msg('失败 '+e,false); }
}
let toastT=0;
async function poll(force){
  if(busy) return; busy=true;
  try{
    let r=await fetch(D+'frame',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({since:shown})});
    if(!r.ok){ r=await fetch(D+'frame?since='+encodeURIComponent(shown)); }
    const d=await r.json();
    if(d.error){ msg(d.error,false); }
    if(!d.same && d.b64){ img.src='data:image/jpeg;base64,'+d.b64; shown=d.hash; }
    if(d.panel && !panelOn){ loadPanel(); }
    if(!d.panel && panelOn){ panelOn=false; document.getElementById('panelbox').style.display='none'; }
    document.getElementById('url').textContent=d.url||'';
    const L=document.getElementById('login');
    L.textContent=d.logged_in?'已登录':'未登录';
    L.className='badge '+(d.logged_in?'on':'off');
    const S=document.getElementById('saved');
    S.textContent=d.saved?'登录态已保存':'未保存';
    S.className='badge '+(d.saved?'on':'off');
  }catch(e){}
  busy=false;
}
poll(); setInterval(poll,2000);
</script></body></html>"""


DISPATCH = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CSDN 一键分发</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#f4f5f7;font:14px/1.6 -apple-system,"Segoe UI","PingFang SC",sans-serif;color:#222}
header{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:10px 14px;background:#fff;border-bottom:1px solid #e3e5e8;position:sticky;top:0;z-index:9}
h1{font-size:15px;margin:0 12px 0 0}
a{color:#1a56b0;text-decoration:none}
button{padding:5px 10px;border:1px solid #c9ced6;background:#fff;border-radius:6px;cursor:pointer;font-size:12px}
button:hover{background:#f0f2f5}
button.go{background:#1a56b0;border-color:#1a56b0;color:#fff}
button.warn{border-color:#b45309;color:#b45309}
.badge{padding:2px 8px;border-radius:10px;font-size:12px;background:#eceff3}
.badge.on{background:#e3f5e9;color:#1a7f37}
.badge.off{background:#fdeaea;color:#b42318}
.badge.warn{background:#fef3c7;color:#b45309}
main{padding:14px;max-width:1100px;margin:0 auto}
.cards{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:12px}
.card{background:#fff;border:1px solid #e3e5e8;border-radius:8px;padding:10px 14px;min-width:130px}
.card b{display:block;font-size:20px}
.card span{font-size:12px;color:#6b7280}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e3e5e8;border-radius:8px;overflow:hidden}
th,td{padding:8px 10px;border-bottom:1px solid #eef0f2;text-align:left;font-size:13px;vertical-align:middle}
th{background:#f8f9fb;color:#555;font-size:12px}
tr:last-child td{border-bottom:0}
.tag{padding:2px 8px;border-radius:10px;font-size:12px;white-space:nowrap}
.tag.published{background:#e3f5e9;color:#1a7f37}
.tag.queued{background:#dbeafe;color:#1a56b0}
.tag.failed{background:#fdeaea;color:#b42318}
.tag.pending{background:#eceff3;color:#4b5563}
.title{max-width:380px}
pre#log{background:#111827;color:#d1d5db;border-radius:8px;padding:10px;font-size:11px;max-height:260px;overflow:auto;display:none;white-space:pre-wrap}
.hint{color:#6b7280;font-size:12px;margin:8px 0}
#msg{font-size:12px;min-height:18px;color:#1a7f37}
</style></head><body>
<header>
  <h1>CSDN 一键分发</h1>
  <span id="b-login" class="badge">登录</span>
  <span id="b-daemon" class="badge">daemon</span>
  <span id="b-quota" class="badge">额度</span>
  <span id="b-window" class="badge">时段</span>
  <span style="flex:1"></span>
  <a href="#" id="loglink">日志</a>
  <a href="relay">登录中转台</a>
</header>
<main>
  <div class="cards">
    <div class="card"><b id="c-today">-</b><span>今日已发 / 上限</span></div>
    <div class="card"><b id="c-queue">-</b><span>队列剩余</span></div>
    <div class="card"><b id="c-csdn">-</b><span>CSDN 已发布总数</span></div>
    <div class="card"><b id="c-succ">-</b><span>本通道累计成功</span></div>
  </div>
  <div class="hint" id="note"></div>
  <table>
    <thead><tr><th>时间</th><th>标题</th><th>状态</th><th>操作</th></tr></thead>
    <tbody id="rows"></tbody>
  </table>
  <pre id="log"></pre>
  <div id="msg"></div>
</main>
<script>
const D=new URL('.',location.href).pathname;
function msg(t,ok){const m=document.getElementById('msg');m.textContent=t||'';m.style.color=ok===false?'#b42318':'#1a7f37';}
async function get(u){const r=await fetch(D+u.replace(/^\//,''));return await r.json();}
async function post(u,body){const r=await fetch(D+u.replace(/^\//,''),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});return await r.json();}
function badge(txt,cls){return '<span class="tag '+cls+'">'+txt+'</span>';}
async function refresh(){
  try{
    const [ov,ar]=await Promise.all([get('/api/overview'),get('/api/articles?days=14')]);
    const set=(id,txt,cls)=>{const e=document.getElementById(id);e.textContent=txt;e.className='badge '+(cls||'');};
    set('b-login',ov.login_ok?'登录态有效':'登录失效',ov.login_ok?'on':'off');
    set('b-daemon',ov.runner_alive?'daemon 运行中':'daemon 未运行',ov.runner_alive?'on':'off');
    set('b-quota',ov.quota_blocked_today?'今日额度已用尽':'今日额度 '+(ov.daily_limit-ov.today_total),ov.quota_blocked_today?'off':'on');
    set('b-window',ov.window.join('-'),'');
    document.getElementById('c-today').textContent=ov.today_total+' / '+ov.daily_limit;
    document.getElementById('c-queue').textContent=ov.queued;
    document.getElementById('c-csdn').textContent=ov.api_ok?(ov.csdn_articles??'-'):'-';
    document.getElementById('c-succ').textContent=(ov.counters&&ov.counters.success)??'-';
    document.getElementById('note').textContent=
      (ov.quota_blocked_today?'平台限额每日 '+ov.daily_limit+' 篇，今日已用尽，置顶任务将于明日 08:00 后自动发出。':
       '点击「置顶发布」后，分发 daemon 会在 60 秒内优先处理（需满足发布时段与额度）。')
      +(!ov.runner_alive?' 注意：分发 daemon 未运行，任务不会自动执行（python3 csdn_runner.py --loop）。':'');
    const tb=document.getElementById('rows');
    tb.innerHTML=ar.rows.map(r=>{
      let act='';
      if(r.status==='published') act='<a href="'+r.url+'" target="_blank">查看</a>';
      else if(r.status==='failed') act='<button class="go" onclick="doQueue(\''+r.article_id+'\')">重试发布</button> <button class="warn" onclick="doLog(\''+r.error.replace(/'/g,"\\'")+'\')">原因</button>';
      else if(r.status==='queued') act=(r.priority?'<span class="hint">已置顶</span> ':'')+'<button class="warn" onclick="doUnqueue(\''+r.article_id+'\')">移出队列</button>';
      else act='<button class="go" onclick="doQueue(\''+r.article_id+'\')">置顶发布</button>';
      const err=r.error?'<div class="hint" style="max-width:340px">'+r.error.slice(0,80)+'</div>':'';
      return '<tr><td>'+r.time+'</td><td class="title">'+r.title+err+'</td><td>'+badge({published:'已发布',queued:r.priority?'队列·置顶':'队列中',failed:'失败',pending:'未分发'}[r.status],r.status)+'</td><td>'+act+'</td></tr>';
    }).join('');
  }catch(e){msg('刷新失败 '+e,false);}
}
async function doQueue(id){
  try{const d=await post('/api/queue',{article_ids:[id]});
    msg((d.added.length?'已置顶 '+d.added.join(','):'')+(d.skipped.length?'，跳过 '+d.skipped.join(','):''));
    setTimeout(refresh,400);}catch(e){msg('操作失败 '+e,false);}
}
async function doUnqueue(id){
  try{const d=await post('/api/unqueue',{article_ids:[id]});
    msg(d.removed.length?'已移出队列':'未在队列中');
    setTimeout(refresh,400);}catch(e){msg('操作失败 '+e,false);}
}
async function doLog(err){
  document.getElementById('log').textContent=err||'(无错误记录)';
  document.getElementById('log').style.display='block';
}
document.getElementById('loglink').onclick=async e=>{
  e.preventDefault();
  const p=document.getElementById('log');
  if(p.style.display==='block'){p.style.display='none';return;}
  const d=await get('/api/log?n=60');
  p.textContent=d.lines.join('\n')||'(空)';
  p.style.display='block';
};
refresh();setInterval(refresh,5000);
</script></body></html>"""


@app.get("/")
def index():
    return Response(DISPATCH, mimetype="text/html; charset=utf-8")


@app.get("/relay")
def relay():
    return Response(INDEX, mimetype="text/html; charset=utf-8")


@app.route("/frame", methods=["GET", "POST"])
def frame():
    d = request.get_json(force=True, silent=True) or {}
    since = str(d.get("since") or request.args.get("since") or "")
    try:
        r = worker.call("shot", timeout=15)
        if since and since == r["hash"]:
            r["b64"] = ""
            r["same"] = True
        else:
            r["same"] = False
        return jsonify(r)
    except Exception as e:
        return jsonify({"error": str(e)})


@app.post("/click")
def click():
    d = request.get_json(force=True, silent=True) or {}
    try:
        return jsonify(worker.call("click", d.get("x", 0), d.get("y", 0)))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.post("/sms")
def sms():
    d = request.get_json(force=True, silent=True) or {}
    try:
        return jsonify(worker.call("sms", str(d.get("code", "")),
                                    bool(d.get("submit", True)), timeout=30))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.post("/click_multi")
def click_multi():
    d = request.get_json(force=True, silent=True) or {}
    try:
        return jsonify(worker.call("click_multi", d.get("points") or [], timeout=40))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.post("/type")
def type_text():
    d = request.get_json(force=True, silent=True) or {}
    try:
        return jsonify(worker.call("type", str(d.get("text", ""))))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.post("/key")
def key():
    d = request.get_json(force=True, silent=True) or {}
    try:
        return jsonify(worker.call("key", str(d.get("key", "Enter"))))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.post("/<name>")
def action(name):
    if name not in ("login", "sendcode", "restart", "editor", "save", "reload",
                    "status", "captcha_info", "refresh_captcha", "dom", "panel"):
        return jsonify({"error": "unknown"}), 404
    try:
        return jsonify(worker.call(name, timeout=90))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.get("/api/overview")
def api_overview():
    return jsonify(_overview())


@app.get("/api/articles")
def api_articles():
    days = min(int(request.args.get("days", 14) or 14), 30)
    return jsonify({"rows": _merged_rows(days)})


def _mutate_queue(mutate):
    q = _load_json(DIST_QUEUE, {"jobs": []})
    q.setdefault("jobs", [])
    result = mutate(q)
    fd, tmp = tempfile.mkstemp(dir=BASE, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(q, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DIST_QUEUE)
    return result


@app.post("/api/queue")
def api_queue():
    d = request.get_json(force=True, silent=True) or {}
    ids = [str(x) for x in (d.get("article_ids") or [])][:10]
    rows = {r["article_id"]: r for r in _merged_rows(30)}
    added, skipped = [], []
    def do(q):
        have = {j["article_id"] for j in q["jobs"]}
        for aid in ids:
            if aid in have:
                for j in q["jobs"]:
                    if j["article_id"] == aid:
                        j["priority"] = True
                added.append(aid)
                continue
            row = rows.get(aid)
            if not row or row["status"] == "published":
                skipped.append(aid)
                continue
            q["jobs"].insert(0, _job_from_row(row))
            added.append(aid)
    _mutate_queue(do)
    return jsonify({"added": added, "skipped": skipped})


@app.post("/api/unqueue")
def api_unqueue():
    d = request.get_json(force=True, silent=True) or {}
    ids = {str(x) for x in (d.get("article_ids") or [])}
    removed = []
    def do(q):
        keep = []
        for j in q["jobs"]:
            if j["article_id"] in ids:
                removed.append(j["article_id"])
            else:
                keep.append(j)
        q["jobs"] = keep
    _mutate_queue(do)
    return jsonify({"removed": removed})


@app.get("/api/log")
def api_log():
    n = min(int(request.args.get("n", 60) or 60), 400)
    path = os.path.join(LOGS, "csdn.log")
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
        return jsonify({"lines": lines[-n:]})
    except OSError:
        return jsonify({"lines": []})


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8300)
    a = ap.parse_args()
    worker.start()
    app.run(host="0.0.0.0", port=a.port, threaded=True)
