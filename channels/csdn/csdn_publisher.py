"""把一篇已发布文章投递到 CSDN Markdown 编辑器。

用法：
  python3 csdn_publisher.py --job job.json --stage preview
  python3 csdn_publisher.py --job job.json

job.json: {"article_id","title","summary","file","cover","body_image_files":[],"category"}

stage: preview=填完发布弹窗但不点确认；publish=直接发布
输出最后一行 JSON。
"""
import argparse
import base64
import json
import os
import re
import time

BASE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE, "storage_state.json")
CONFIG_FILE = os.path.join(BASE, "config.json")
LOGS = os.path.join(BASE, "logs")
EDITOR_URL = "https://editor.csdn.net/md/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
IMG_RE = r"https://i-blog\.csdnimg\.cn/direct/[A-Za-z0-9._-]+\.(?:jpeg|jpg|png|webp)"


def log(msg):
    os.makedirs(LOGS, exist_ok=True)
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    with open(os.path.join(LOGS, "csdn.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def load_job(path):
    job = _load_json(path, None)
    if not job:
        raise ValueError("job 文件不可读: " + path)
    with open(job["file"], encoding="utf-8") as f:
        raw = f.read()
    parts = raw.split("\n", 1)
    job["body"] = parts[1].strip() if len(parts) > 1 else raw
    job["title"] = job.get("title") or parts[0].lstrip("# ").strip()
    return job


def validate(job):
    errs = []
    if len(job.get("title") or "") < 5:
        errs.append("标题少于 5 字")
    if len(job.get("body") or "") < 200:
        errs.append("正文过短")
    if not job.get("cover") or not os.path.exists(job["cover"]):
        errs.append("封面文件不存在")
    for p in job.get("body_image_files") or []:
        if not os.path.exists(p):
            errs.append("正文图片不存在: " + p)
    return errs


def _launch(p):
    kw = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
    px = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if px:
        kw["proxy"] = {"server": px}
    return p.chromium.launch(**kw)


def _dismiss_guide(page):
    for t in ("我知道了", "跳过", "知道了"):
        loc = page.get_by_text(t, exact=True)
        if loc.count():
            try:
                loc.first.click(timeout=3000)
                page.wait_for_timeout(600)
            except Exception:
                pass
    try:
        page.evaluate("()=>{const g=document.querySelector('.beginnerGuide-box');if(g)g.remove();}")
    except Exception:
        pass


def _fill_title(page, title):
    el = page.query_selector(".article-bar__title-display")
    if not el:
        raise RuntimeError("未找到标题区")
    el.click()
    page.wait_for_timeout(200)
    page.keyboard.press("Control+A")
    page.keyboard.insert_text(title)
    page.wait_for_timeout(400)
    val = page.evaluate("()=>{const e=document.querySelector('input.article-bar__title');return e?e.value:''}")
    if title[:6] not in (val or ""):
        raise RuntimeError("标题写入失败: " + (val or "")[:40])


def _set_editor_text(page, text):
    el = page.query_selector("pre.editor__inner")
    if not el:
        raise RuntimeError("未找到正文编辑器")
    el.click()
    page.keyboard.press("Control+A")
    page.wait_for_timeout(150)
    ok = page.evaluate("""(text)=>{
      const el=document.querySelector('pre.editor__inner');
      if(!el) return false;
      el.focus();
      const dt=new DataTransfer(); dt.setData('text/plain', text);
      const ev=new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true});
      el.dispatchEvent(ev);
      return ev.defaultPrevented;}""", text)
    if not ok:
        raise RuntimeError("正文粘贴未被编辑器接收")
    page.wait_for_timeout(900)
    txt = _editor_text(page)
    if "欢迎使用Markdown编辑器" in txt or len(txt) < max(200, int(len(text) * 0.6)):
        page.keyboard.press("Control+A")
        page.wait_for_timeout(150)
        page.evaluate("""(text)=>{
          const el=document.querySelector('pre.editor__inner');
          el.focus();
          const dt=new DataTransfer(); dt.setData('text/plain', text);
          el.dispatchEvent(new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true}));}""", text)
        page.wait_for_timeout(900)
        txt = _editor_text(page)
        if "欢迎使用Markdown编辑器" in txt:
            raise RuntimeError("正文写入失败，编辑器残留模板内容")
    return len(txt)


def _write_body(page, body):
    return _set_editor_text(page, body)


def _editor_text(page):
    return page.evaluate("()=>{const e=document.querySelector('pre.editor__inner');return e?e.innerText:''}")


def _paste_image(page, path):
    b64 = base64.b64encode(open(path, "rb").read()).decode()
    name = os.path.basename(path)
    return page.evaluate("""([b64,name])=>{
      const bin=atob(b64); const arr=new Uint8Array(bin.length);
      for(let i=0;i<bin.length;i++) arr[i]=bin.charCodeAt(i);
      const file=new File([arr],name,{type:'image/jpeg'});
      const dt=new DataTransfer(); dt.items.add(file);
      const el=document.querySelector('pre.editor__inner');
      if(!el) return false;
      el.focus();
      const ev=new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true});
      el.dispatchEvent(ev);
      return ev.defaultPrevented;
    }""", [b64, name])


def _upload_images(page, files):
    if not files:
        return []
    page.query_selector("pre.editor__inner").click()
    page.keyboard.press("Control+End")
    page.wait_for_timeout(300)
    before = len(re.findall(IMG_RE, _editor_text(page)))
    for f in files:
        page.query_selector("pre.editor__inner").click()
        page.keyboard.press("Control+End")
        page.wait_for_timeout(200)
        ok = _paste_image(page, f)
        log("粘贴图片 %s paste=%s" % (os.path.basename(f), ok))
        deadline = time.time() + 40
        while time.time() < deadline:
            page.wait_for_timeout(1500)
            if len(re.findall(IMG_RE, _editor_text(page))) > before:
                break
        before = len(re.findall(IMG_RE, _editor_text(page)))
    urls = re.findall(IMG_RE, _editor_text(page))
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _apply_images(page, body, urls):
    for i, u in enumerate(urls[:2], 1):
        body = body.replace("__CSDN_IMG_%d__" % i, u)
        body = body.replace("[[IMG%d]]" % i, u)
    body = re.sub(r"__CSDN_IMG_\d+__", "", body)
    body = re.sub(r"\[\[IMG\d+\]\]", "", body)
    _set_editor_text(page, body)
    return body


def _open_publish_dialog(page):
    btn = page.query_selector("button.btn-publish")
    if not btn:
        raise RuntimeError("未找到发布按钮")
    btn.click()
    page.wait_for_timeout(3000)


def _set_tags(page, tags):
    if not tags:
        return 0
    try:
        btn = page.query_selector(".tag__btn-tag")
        if not btn:
            return 0
        btn.click()
        page.wait_for_timeout(600)
        inp = page.get_by_placeholder(re.compile("Enter键入")).first
        n = 0
        for t in tags[:5]:
            inp.click()
            inp.fill(t)
            page.keyboard.press("Enter")
            page.wait_for_timeout(700)
            n += 1
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        page.evaluate("""()=>{const m=document.querySelector('.modal');
          const h=m&&m.querySelector('.modal-title,.title,.ant-modal-title');if(h)h.click();else if(m)m.click();}""")
        page.wait_for_timeout(700)
        return n
    except Exception as e:
        log("标签设置失败: " + str(e)[:120])
        return 0


def _set_summary(page, text):
    if not text:
        return False
    try:
        ta = page.get_by_placeholder(re.compile("本内容会在各展现列表")).first
        if ta.count():
            ta.fill(text[:256])
            return True
    except Exception:
        pass
    try:
        ok = page.evaluate("""(text)=>{const m=document.querySelector('.modal')||document;
          const ta=[...m.querySelectorAll('textarea')].find(e=>e.offsetParent!==null);
          if(!ta) return false; ta.focus();
          const d=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value');
          d.set.call(ta,text);
          ta.dispatchEvent(new Event('input',{bubbles:true}));
          ta.dispatchEvent(new Event('change',{bubbles:true}));
          return true;}""", text[:256])
        if not ok:
            log("摘要设置失败: 未找到摘要输入框")
        return bool(ok)
    except Exception as e:
        log("摘要设置失败: " + str(e)[:120])
        return False


def _cover_imgs(page):
    try:
        return page.evaluate("""()=>{const b=document.querySelector('.container-coverimage-box');
          if(!b) return [];
          return [...b.querySelectorAll('img')].filter(e=>/csdnimg\.cn\/direct/.test(e.src))
            .map(e=>e.src);}""") or []
    except Exception:
        return []


def _crop_visible(page):
    try:
        return bool(page.evaluate("""()=>{const c=document.querySelector('.vicp-crop');
          if(!c) return false;const r=c.getBoundingClientRect();
          return c.offsetParent!==null&&r.width>4&&r.height>4;}"""))
    except Exception:
        return False


def _confirm_cover(page):
    try:
        return bool(page.evaluate("""()=>{
          const els=[...document.querySelectorAll('button,span,div,a')]
            .filter(e=>/^确认上传$/.test((e.innerText||'').trim()) && e.offsetParent!==null);
          if(!els.length) return false;
          els[els.length-1].click(); return true;}"""))
    except Exception:
        return False


def _new_cover(page, before, timeout=40):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for u in _cover_imgs(page):
            if u not in before:
                return u
        page.wait_for_timeout(1200)
    return ""


def _set_cover(page, path):
    if not path:
        return False
    before = set(_cover_imgs(page))
    set_ok = False
    err = ""
    for _ in range(3):
        try:
            with page.expect_file_chooser(timeout=6000) as fc:
                page.click("button.upload-img-box", timeout=6000)
            fc.value.set_files(path)
            set_ok = True
            break
        except Exception as e:
            err = str(e)[:90]
            try:
                el = page.query_selector("input.cfw-file-input")
                if el:
                    el.set_input_files(path)
                    set_ok = True
                    break
            except Exception:
                pass
            page.wait_for_timeout(800)
    if not set_ok:
        log("封面上传失败: " + err)
        return False
    deadline = time.time() + 15
    while time.time() < deadline and not _crop_visible(page) and not _confirm_cover(page):
        page.wait_for_timeout(700)
    if not _confirm_cover(page):
        log("封面：未找到确认上传按钮")
    deadline = time.time() + 30
    while time.time() < deadline:
        if not _crop_visible(page):
            break
        page.wait_for_timeout(700)
    url = _new_cover(page, before, 40)
    if not url:
        log("封面：上传后未出现新封面")
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(800)
    except Exception:
        pass
    return bool(url)


def _set_original(page):
    try:
        loc = page.locator(".modal").get_by_text("原创", exact=True)
        if loc.count():
            loc.first.click(timeout=4000)
            page.wait_for_timeout(400)
            return True
    except Exception:
        pass
    return False


def _dbg(page, tag):
    if not os.environ.get("CSDN_DEBUG"):
        return
    try:
        txt = page.evaluate("""()=>{const m=document.querySelector('.modal, .el-dialog, .csdn-modal');return m?m.innerText.replace(/\s+/g,' ').slice(0,300):'';}""")
    except Exception:
        txt = ""
    log("[dbg] %s url=%s modal=%s" % (tag, page.url, txt))


def _confirm(page):
    cand = page.get_by_role("button", name="发布文章", exact=True)
    if cand.count():
        loc = cand.last
    else:
        loc = page.locator("button.btn-b-red.ml16").last
    if loc is None or not loc.count():
        _dbg(page, "confirm=no-button")
        return False
    hits = []
    def _on_req(r):
        if "saveArticle" in r.url:
            hits.append(["req", 0])
    def _on_resp(r):
        if "saveArticle" in r.url:
            hits.append(["resp", r.status])
    page.on("request", _on_req)
    page.on("response", _on_resp)
    try:
        loc.click(timeout=8000)
    except Exception as e:
        log("点击发布按钮失败: " + str(e)[:100])
        page.remove_listener("request", _on_req); page.remove_listener("response", _on_resp)
        return False
    page.wait_for_timeout(7000)
    txt = page.evaluate("()=>document.body.innerText")
    if "发文上限" in txt or "发文上限" in page.content():
        log("CSDN 提示已达发文上限")
        return "quota"
    left = page.evaluate("()=>!!document.querySelector('.modal')")
    if left:
        info = page.evaluate("""()=>({
          toast:[...document.querySelectorAll('.el-message,.toast,.message,.ant-message-notice,.layui-layer')]
            .map(e=>(e.innerText||'').trim()).filter(Boolean).slice(0,3).join(' | '),
          errs:[...document.querySelectorAll('.el-form-item__error,[class*=error-tip],[class*=tip-error]')]
            .map(e=>(e.innerText||'').trim()).filter(Boolean).slice(0,4).join(' | '),
          btn:[...document.querySelectorAll('button')].filter(b=>/发布文章/.test(b.innerText||'')&&b.offsetParent!==null)
            .map(b=>({cls:b.className,dis:!!b.disabled})).slice(0,3)})""")
        log("确认后弹窗未关闭 hits=%s %s" % (hits, str(info)[:200]))
    _dbg(page, "after-confirm")
    page.remove_listener("request", _on_req); page.remove_listener("response", _on_resp)
    return not left


def _article_url(page, before_url):
    for i in range(10):
        _dbg(page, "url-poll-%d" % i)
        m = re.search(r"https://blog\.csdn\.net/[^/\s]+/article/details/\d+", page.url)
        if m:
            return m.group(0)
        try:
            link = page.evaluate("""()=>{
              const a=[...document.querySelectorAll('a')].find(e=>/article\\/details\\/\\d+/.test(e.href));
              return a?a.href:'';}""")
            if link:
                return link
        except Exception:
            pass
        page.wait_for_timeout(1500)
    return page.url if page.url != before_url else ""


def publish(job, stage="publish", article_id=None):
    cfg = _load_json(CONFIG_FILE, {})
    cat = job.get("category", "")
    cmap = cfg.get("column_map") or {}
    col_name = cmap.get(cat, cat)
    entry = (cfg.get("columns") or {}).get(col_name) or (cfg.get("columns") or {}).get(cat) or {}
    job["column"] = job.get("column") or (entry.get("name") if isinstance(entry, dict) else entry) or ""
    job["column_id"] = entry.get("id") if isinstance(entry, dict) else None
    job["tags"] = job.get("tags") or (cfg.get("tags") or {}).get(cat) or [cat, "产业观察", "数字化转型"]
    job["tags"] = [t for t in job["tags"] if t][:5]

    body = job["body"]
    files = job.get("body_image_files") or []
    for i in range(1, len(files) + 1):
        body = body.replace("[[IMG%d]]" % i, "__CSDN_IMG_%d__" % i)
    job["body"] = body

    if stage == "dry-run":
        return {"ok": True, "dry_run": True, "title": job["title"],
                "body_len": len(body), "images": len(files), "cover": job.get("cover"),
                "column": job.get("column"), "tags": job["tags"]}

    if not os.path.exists(STATE_FILE):
        raise RuntimeError("缺少登录态，请先运行 csdn_login.py --login")

    from playwright.sync_api import sync_playwright
    warnings = []
    with sync_playwright() as p:
        b = _launch(p)
        ctx = b.new_context(user_agent=UA, viewport={"width": 1440, "height": 900},
                            storage_state=STATE_FILE)
        page = ctx.new_page()
        url_open = EDITOR_URL + (("?articleId=%s" % article_id) if article_id else "")
        page.goto(url_open, wait_until="commit", timeout=60000)
        page.wait_for_timeout(6000)
        if "login" in page.url:
            b.close()
            raise RuntimeError("登录态失效，需要重新登录")
        _dismiss_guide(page)

        _fill_title(page, job["title"])
        _write_body(page, job["body"].replace("__CSDN_IMG_1__", "").replace("__CSDN_IMG_2__", ""))
        page.wait_for_timeout(800)

        urls = _upload_images(page, files)
        if len(urls) < len(files):
            warnings.append("正文图片上传 %d/%d" % (len(urls), len(files)))
        _apply_images(page, job["body"], urls)
        page.wait_for_timeout(1500)

        _open_publish_dialog(page)
        _dbg(page, "dialog-open")
        cover_ok = _set_cover(page, job.get("cover"))
        if not cover_ok:
            warnings.append("封面未上传")
        n_tags = _set_tags(page, job["tags"])
        if n_tags == 0:
            warnings.append("标签未设置")
        summary_ok = _set_summary(page, job.get("summary") or "")
        if not summary_ok:
            warnings.append("摘要未设置")
        _set_original(page)
        _dbg(page, "dialog-filled")
        if job.get("column"):
            warnings.append("专栏被平台限制，未挂载: " + job["column"])

        if stage == "preview":
            shot = os.path.join(LOGS, "publish_preview.png")
            page.screenshot(path=shot, full_page=False, timeout=20000)
            b.close()
            return {"ok": True, "stage": "preview", "warnings": warnings,
                    "urls": urls, "tags": n_tags, "cover": cover_ok,
                    "summary": summary_ok, "column": job.get("column")}

        before = page.url
        cst = _confirm(page)
        if cst == "quota":
            page.screenshot(path=os.path.join(LOGS, "publish_quota.png"), full_page=False, timeout=20000)
            b.close()
            return {"ok": False, "url": "", "warnings": warnings, "error": "发文上限"}
        if not cst:
            page.screenshot(path=os.path.join(LOGS, "publish_confirm.png"), full_page=True)
            b.close()
            raise RuntimeError("未找到确认发布按钮")
        page.wait_for_timeout(4000)
        url = _article_url(page, before)
        page.screenshot(path=os.path.join(LOGS, "publish_result.png"), full_page=True, timeout=20000)
        b.close()

    ok = bool(url) and "/article/details/" in url
    return {"ok": ok, "url": url, "warnings": warnings,
            "error": "" if ok else "未获取到文章 URL"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    ap.add_argument("--stage", default="publish", choices=["dry-run", "preview", "publish"])
    ap.add_argument("--article-id", default="")
    a = ap.parse_args()
    try:
        job = load_job(a.job)
        errs = validate(job)
        if errs:
            print(json.dumps({"ok": False, "error": "; ".join(errs)}, ensure_ascii=False))
            return 2
        res = publish(job, stage=a.stage, article_id=a.article_id or None)
    except Exception as e:
        log("发布异常: " + str(e)[:300])
        res = {"ok": False, "error": str(e)[:300]}
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
