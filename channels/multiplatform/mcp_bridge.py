"""多平台一键分发桥（对接「CSDN同步助手」v1.2.1 扩展的 MCP 协议）。

扩展是 WS 客户端，默认连 ws://127.0.0.1:3721?token=...；本程序是 WS 服务端，
在运行 Chrome+扩展的电脑上执行，负责：
  1. 监测各平台登录态（listPlatforms / checkAuth），写 state.json，提供本地看板
  2. 一键同步文章到已登录平台（syncArticle，MCP 仅支持草稿，各平台后台再点发布）

用法：
  python3 mcp_bridge.py serve                 # 常驻：WS 服务 + 本地看板 + 登录态监测
  python3 mcp_bridge.py platforms             # 列出平台与登录态
  python3 mcp_bridge.py sync  --platform juejin article.md [--title T] [--cover U] [--tags a,b] [--retry]
  python3 mcp_bridge.py sync-all article.md [--platforms a,b] [--title T] [--cover U] [--tags a,b]

依赖：pip3 install websockets
说明：sync/sync-all 优先走运行中的 serve（HTTP API），否则自动起临时 WS 等扩展连接。
"""
import argparse
import asyncio
import base64
import hashlib
import json
import mimetypes
import os
import re
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    try:
        from websockets.asyncio.server import serve as ws_serve  # v13+
    except ImportError:
        from websockets.server import serve as ws_serve  # v10-12
except ImportError:
    ws_serve = None

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE, "config.json")
STATE_FILE = os.path.join(BASE, "state.json")

DEFAULTS = {
    "ws_port": 3721,
    "http_port": 8390,
    "token": "",
    "monitor_interval_sec": 600,
    "request_timeout_sec": 300,
    "standalone_wait_sec": 90,
}

# 平台元数据（逆向自扩展 v1.2.1；isAiGraphic 的两个图文变体不支持 MCP）
PLATFORMS = [
    ("zhihu", "知乎", "https://www.zhihu.com", "html"),
    ("juejin", "掘金", "https://juejin.cn", "markdown"),
    ("weibo", "微博", "https://weibo.com", "html"),
    ("toutiao", "头条", "https://mp.toutiao.com", "html"),
    ("baijiahao", "百家号", "https://baijiahao.baidu.com", "html"),
    ("wechat", "微信公众号", "https://mp.weixin.qq.com", "html"),
    ("cnblogs", "博客园", "https://www.cnblogs.com", "markdown"),
    ("51cto", "51CTO", "https://www.51cto.com", "markdown"),
    ("jianshu", "简书", "https://www.jianshu.com", "html"),
    ("segmentfault", "思否", "https://segmentfault.com", "markdown"),
    ("oschina", "开源中国", "https://www.oschina.net", "markdown"),
    ("tencentcloud", "腾讯云开发者", "https://cloud.tencent.com/developer", "markdown"),
    ("aliyun", "阿里云开发者", "https://developer.aliyun.com", "markdown"),
    ("netease", "网易号", "https://www.163.com", "html"),
    ("bilibili", "哔哩哔哩", "https://www.bilibili.com", "html"),
    ("douyin", "抖音长文", "https://creator.douyin.com", "markdown"),
    ("xiaohongshu", "小红书长文", "https://www.xiaohongshu.com", "markdown"),
]
PLATFORM_IDS = [p[0] for p in PLATFORMS]
NAME_BY_ID = {p[0]: p[1] for p in PLATFORMS}


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(patch):
    st = load_state()
    st.update(patch)
    st["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    fd, tmp = tempfile.mkstemp(dir=BASE, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


class ExtBridge:
    """与扩展的 WS 连接管理。"""

    def __init__(self, token=""):
        self.token = token
        self.conn = None
        self.pending = {}

    def connected(self):
        return self.conn is not None

    def on_connect(self, ws, token_seen):
        self.conn = ws
        save_state({"connected": True, "connected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "ext_token_set": bool(token_seen)})

    def on_disconnect(self, ws):
        if self.conn is ws:
            self.conn = None
            for fut in self.pending.values():
                if not fut.done():
                    fut.set_exception(RuntimeError("扩展连接断开"))
            self.pending.clear()
            save_state({"connected": False})

    def on_message(self, ws, raw):
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return
        if not isinstance(msg, dict):
            return
        if msg.get("type") == "ping":
            asyncio.get_running_loop().create_task(ws.send(json.dumps({"type": "pong"})))
            return
        if msg.get("type") == "pong":
            save_state({"last_pong": time.strftime("%H:%M:%S")})
            return
        mid = msg.get("id")
        fut = self.pending.pop(mid, None) if isinstance(mid, str) else None
        if fut is not None and not fut.done():
            fut.set_result(msg)

    async def request(self, method, params=None, timeout=120):
        if not self.conn:
            raise RuntimeError("扩展未连接：请确认 Chrome 已打开、扩展已启用且指向本服务")
        mid = str(uuid.uuid4())
        fut = asyncio.get_running_loop().create_future()
        self.pending[mid] = fut
        frame = {"id": mid, "token": self.token, "method": method, "params": params or {}}
        try:
            await self.conn.send(json.dumps(frame, ensure_ascii=False))
            return await asyncio.wait_for(fut, timeout)
        finally:
            self.pending.pop(mid, None)

    def auth_result(self, msg, platform):
        res = msg.get("result") or {}
        if "error" in msg:
            return {"authenticated": False,
                    "error": (msg["error"] or {}).get("message", "unknown")}
        out = {"authenticated": bool(res.get("authenticated"))}
        if res.get("error"):
            out["error"] = res["error"]
        for k in ("username", "nickname", "account", "member"):
            if res.get(k):
                out[k] = res[k]
        return out


def extract_title(md):
    for line in md.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
        if line.startswith("#"):
            return line.lstrip("# ").strip()
    return ""


def replace_local_images(md, path_base):
    """把 markdown 中存在的本地图片路径上传占位，返回 (新md, 待上传列表)。"""
    todo = []

    def pick(m):
        src = m.group(2).strip()
        if re.match(r"^(https?:|data:)", src):
            return m.group(0)
        p = src if os.path.isabs(src) else os.path.join(path_base, src)
        p = os.path.normpath(p)
        if os.path.isfile(p):
            todo.append((m.group(0), p))
            return "__LOCAL_IMG_%d__" % (len(todo) - 1)
        return m.group(0)

    out = re.sub(r"(!\[[^\]]*\]\()([^)\s]+)(\))", pick, md)
    return out, todo


def idempotency_key(title, md, platforms):
    raw = "|".join([title, md[:300], ",".join(sorted(platforms))])
    return hashlib.sha1(raw.encode()).hexdigest()


def parse_article_file(args, platform_ids):
    """读取文章文件，返回原始 markdown（本地图片替换由执行端负责）。"""
    md = open(args.file, encoding="utf-8").read()
    title = args.title or extract_title(md)
    if not title:
        raise SystemExit("未找到标题：首行用 # 标题 或传 --title")
    tags = [t.strip() for t in (args.tags or "").split(",") if t.strip()]
    return {"title": title, "markdown": md,
            "local_base": os.path.dirname(os.path.abspath(args.file)),
            "cover": args.cover or "", "tags": tags, "platforms": platform_ids,
            "retry": bool(args.retry)}


def attach_local_images(art):
    """把本地图片路径替换为占位符，记录待上传列表（standalone 模式用）。"""
    md2, todo = replace_local_images(art["markdown"], art.pop("local_base", ""))
    art["markdown"] = md2
    art["todo_images"] = todo
    return art


def build_sync_args(platform, argv):
    """供 sync_<platform>.py 薄封装使用。"""
    ap = argparse.ArgumentParser(
        description="一键同步「%s」(%s)" % (NAME_BY_ID.get(platform, platform), platform))
    ap.add_argument("file", help="markdown 文章文件")
    ap.add_argument("--title", help="标题（默认取首行 # 标题）")
    ap.add_argument("--cover", help="封面图 URL")
    ap.add_argument("--tags", help="标签，逗号分隔")
    ap.add_argument("--retry", action="store_true", help="失败重试")
    a = ap.parse_args(argv)
    a.platform = platform
    return a


async def do_sync(bridge, art, cfg, log=print):
    platforms = art["platforms"]
    md = art["markdown"]
    for i, (orig, p) in enumerate(art.get("todo_images") or []):
        if os.path.getsize(p) > 5 * 1024 * 1024:
            log("图片超过 5MB 跳过上传: %s" % p)
            continue
        mime = mimetypes.guess_type(p)[0] or "image/jpeg"
        with open(p, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        first = platforms[0]
        try:
            r = await bridge.request("uploadImage",
                                     {"imageData": b64, "mimeType": mime, "platform": first},
                                     timeout=120)
            if "error" in r:
                log("图片上传失败(%s): %s" % (os.path.basename(p),
                                             (r["error"] or {}).get("message")))
                continue
            url = (r.get("result") or {}).get("url", "")
            if url:
                md = md.replace("__LOCAL_IMG_%d__" % i, url)
                log("图片已上传: %s -> %s" % (os.path.basename(p), url[:60]))
            else:
                log("图片上传无 URL: %s" % os.path.basename(p))
        except Exception as e:
            log("图片上传异常(%s): %s" % (os.path.basename(p), e))
    md = re.sub(r"__LOCAL_IMG_\d+__", "", md)

    params = {
        "idempotencyKey": idempotency_key(art["title"], md, platforms)[:128],
        "platforms": platforms,
        "article": {"title": art["title"], "markdown": md},
    }
    if art.get("cover"):
        params["article"]["cover"] = art["cover"]
    if art.get("tags"):
        params["article"]["tags"] = art["tags"]
    if art.get("retry"):
        params["retry"] = True
    log("同步到 %d 个平台: %s" % (len(platforms), ", ".join(
        NAME_BY_ID.get(p, p) for p in platforms)))
    r = await bridge.request("syncArticle", params,
                             timeout=int(cfg.get("request_timeout_sec", 300)))
    return r


def print_sync_result(r):
    print(json.dumps(r, ensure_ascii=False, indent=2)[:3000])
    save_state({"last_sync": {"time": time.strftime("%Y-%m-%d %H:%M:%S"),
                              "result": r}})


async def auth_snapshot(bridge, cfg):
    r = await bridge.request("listPlatforms", {"forceRefresh": True},
                             timeout=180)
    if "error" in r:
        raise RuntimeError((r["error"] or {}).get("message", "listPlatforms 失败"))
    plats = {}
    for e in r.get("result") or []:
        pid = e.get("id")
        if not pid:
            continue
        item = {"name": e.get("name", NAME_BY_ID.get(pid, pid)),
                "authenticated": bool(e.get("authenticated")),
                "supports": e.get("supports", [])}
        if e.get("error"):
            item["error"] = e["error"]
        if e.get("preconditions"):
            item["preconditions"] = e["preconditions"]
        plats[pid] = item
    return plats


def ws_handler_factory(bridge):
    from urllib.parse import urlparse, parse_qs

    async def handler(ws, path=None):
        if path is None:
            try:
                path = ws.request.path
            except AttributeError:
                path = ""
        q = parse_qs(urlparse(path).query)
        seen = (q.get("token") or [""])[0]
        tok = bridge.token
        if tok and seen != tok:
            try:
                await ws.close(1008, "token mismatch")
            except Exception:
                pass
            return
        bridge.on_connect(ws, seen)
        try:
            async for raw in ws:
                bridge.on_message(ws, raw)
        except Exception:
            pass
        finally:
            bridge.on_disconnect(ws)
    return handler


async def run_ws_server(bridge, port, wait_conn=True, wait_sec=90):
    srv = await ws_serve(ws_handler_factory(bridge), "127.0.0.1", port)
    if wait_conn:
        t0 = time.time()
        while not bridge.connected() and time.time() - t0 < wait_sec:
            await asyncio.sleep(0.5)
    return srv


async def close_server(srv):
    try:
        srv.close()
    except Exception:
        pass
    try:
        await srv.wait_closed()
    except Exception:
        pass


def sync_via_http(cfg, art):
    import urllib.request
    body = {"title": art["title"], "markdown": art["markdown"],
            "platforms": art["platforms"], "cover": art.get("cover", ""),
            "tags": art.get("tags", [])}
    req = urllib.request.Request(
        "http://127.0.0.1:%d/api/sync" % cfg["http_port"],
        data=json.dumps(body, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=cfg.get("request_timeout_sec", 300) + 30) as r:
        return json.loads(r.read().decode())


def http_bridge_alive(cfg):
    import urllib.request
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/api/status" % cfg["http_port"], timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


async def cmd_sync(args):
    cfg = load_config()
    if ws_serve is None:
        raise SystemExit("缺少依赖：pip3 install websockets")
    platform_ids = [args.platform]
    art = parse_article_file(args, platform_ids)
    if http_bridge_alive(cfg):
        print("（走常驻桥）")
        r = sync_via_http(cfg, art)
        print_sync_result(r)
        return
    print("常驻桥未运行，启动临时 WS 等待扩展连接（最多 %ds）..." % cfg["standalone_wait_sec"])
    bridge = ExtBridge(cfg["token"])
    srv = await run_ws_server(bridge, cfg["ws_port"], wait_sec=cfg["standalone_wait_sec"])
    try:
        if not bridge.connected():
            raise SystemExit("等待超时：扩展未连接（确认扩展已启用、Chrome 已打开）")
        r = await do_sync(bridge, attach_local_images(art), cfg)
        print_sync_result(r)
    finally:
        await close_server(srv)


DASH = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>多平台分发桥</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#f4f5f7;font:14px/1.6 -apple-system,"PingFang SC",sans-serif;color:#222}
header{background:#1f3b8f;color:#fff;padding:12px 16px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
h1{font-size:16px;margin:0}
.badge{padding:2px 10px;border-radius:10px;font-size:12px;background:rgba(255,255,255,.2)}
.badge.on{background:#19be6b}.badge.off{background:#e05252}
main{padding:14px;max-width:1050px;margin:0 auto;display:flex;flex-direction:column;gap:12px}
.card{background:#fff;border:1px solid #e3e5e8;border-radius:8px;padding:12px 14px}
h2{font-size:13px;margin:0 0 8px;color:#555}
table{width:100%;border-collapse:collapse}
th,td{padding:6px 8px;border-bottom:1px solid #eef0f2;font-size:13px;text-align:left}
th{color:#888;font-size:12px;background:#f8f9fb}
.tag{padding:1px 8px;border-radius:9px;font-size:12px}
.tag.on{background:#e3f5e9;color:#1a7f37}.tag.off{background:#fdeaea;color:#b42318}
textarea{width:100%;min-height:120px;border:1px solid #c9ced6;border-radius:6px;padding:8px;font:12px/1.5 ui-monospace,monospace}
input[type=text]{padding:6px 8px;border:1px solid #c9ced6;border-radius:6px;width:100%}
button{padding:7px 16px;border:0;border-radius:6px;cursor:pointer;font-size:13px}
button.go{background:#1f3b8f;color:#fff}button.go:hover{background:#17307a}
button.sec{background:#eceff3;color:#333}
label.ck{margin-right:10px;font-size:13px;user-select:none}
label.ck.dis{color:#aab}
pre{background:#111827;color:#d1d5db;border-radius:8px;padding:10px;font-size:11px;max-height:240px;overflow:auto;white-space:pre-wrap}
.warn{background:#fff7e6;border:1px solid #ffd591;color:#ad6800;border-radius:6px;padding:8px 10px;font-size:12px}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
#msg{font-size:12px;min-height:18px;color:#1a7f37}
</style></head><body>
<header>
  <h1>多平台分发桥</h1>
  <span id="b-conn" class="badge off">扩展未连接</span>
  <span id="b-auth" class="badge">已登录 0</span>
</header>
<main>
  <div class="warn">前置：Chrome 保持打开且有一个普通网页标签页处于激活状态（扩展靠它做内容预处理）；同步结果为各平台草稿，去各平台后台点发布。</div>
  <div class="card">
    <h2>平台登录态（自动监测）</h2>
    <table><thead><tr><th>平台</th><th>登录态</th><th>能力</th><th>提示</th></tr></thead>
    <tbody id="rows"></tbody></table>
  </div>
  <div class="card">
    <h2>一键同步</h2>
    <div class="row" style="margin-bottom:8px">
      <input type="text" id="title" placeholder="标题（留空取 markdown 首行 # 标题）" style="flex:1;min-width:240px">
      <input type="text" id="cover" placeholder="封面 URL（可选）" style="flex:1;min-width:200px">
      <input type="text" id="tags" placeholder="标签，逗号分隔（可选）">
    </div>
    <textarea id="md" placeholder="粘贴 Markdown 正文，或"></textarea>
    <div class="row" style="margin:8px 0">
      <input type="file" id="file" accept=".md,.markdown,.txt" style="font-size:12px">
      <span id="fmsg" style="font-size:12px;color:#6b7280"></span>
    </div>
    <div id="cks" class="row" style="margin-bottom:8px"></div>
    <div class="row">
      <button class="go" onclick="doSync(false)">同步到勾选平台</button>
      <button class="sec" onclick="doSync(true)">一键同步到全部已登录</button>
      <button class="sec" onclick="checkAuth()">刷新登录态</button>
      <span id="msg"></span>
    </div>
  </div>
  <div class="card" id="rescard" style="display:none">
    <h2>最近一次同步结果</h2><pre id="res"></pre>
  </div>
</main>
<script>
function msg(t,ok){const m=document.getElementById('msg');m.textContent=t||'';m.style.color=ok===false?'#b42318':'#1a7f37';}
async function get(u){const r=await fetch(u);return await r.json();}
async function post(u,body){const r=await fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});return await r.json();}
document.getElementById('file').onchange=e=>{
  const f=e.target.files[0]; if(!f)return;
  const rd=new FileReader();
  rd.onload=()=>{document.getElementById('md').value=rd.result;
    document.getElementById('fmsg').textContent='已载入 '+f.name;};
  rd.readAsText(f,'utf-8');
};
let authCount=0;
async function refresh(){
  try{
    const s=await get('api/status');
    const c=document.getElementById('b-conn');
    c.textContent=s.connected?'扩展已连接':'扩展未连接';
    c.className='badge '+(s.connected?'on':'off');
    const rows=document.getElementById('rows');
    const plats=s.platforms||{};
    const ids=Object.keys(plats).sort();
    authCount=ids.filter(id=>plats[id].authenticated).length;
    document.getElementById('b-auth').textContent='已登录 '+authCount;
    rows.innerHTML=ids.map(id=>{
      const p=plats[id];
      const sup=(p.supports||[]).filter(x=>['draft','publish','image_upload','tags','cover','categories'].includes(x)).join(',');
      return '<tr><td>'+ (p.name||id) +'</td><td><span class="tag '+(p.authenticated?'on">已登录':'off">未登录')+
        '</span></td><td style="color:#888">'+sup+'</td><td style="color:#b45309;font-size:12px">'+(p.error||'')+'</td></tr>';
    }).join('');
    const cks=document.getElementById('cks');
    if(!cks.dataset.built){
      cks.innerHTML=ids.map(id=>'<label class="ck'+(plats[id].authenticated?'':' dis')+'"><input type="checkbox" value="'+id+'" '+(plats[id].authenticated?'checked':'disabled')+'> '+ (plats[id].name||id) +'</label>').join('');
      cks.dataset.built='1';
    } else {
      cks.querySelectorAll('input').forEach(ck=>{
        const p=plats[ck.value];
        ck.disabled=!p.authenticated; ck.checked=!!p.authenticated;
        ck.parentElement.className='ck'+(p.authenticated?'':' dis');
      });
    }
  }catch(e){}
}
async function checkAuth(){
  msg('刷新中...');
  try{const d=await post('api/check-auth');msg(d.ok?'已刷新':(d.error||'失败'),!!d.ok);refresh();}
  catch(e){msg('刷新失败 '+e,false);}
}
async function doSync(all){
  let plats=[...document.querySelectorAll('#cks input:checked')].map(i=>i.value);
  if(!plats.length){msg('没有已登录平台可同步',false);return;}
  const md=document.getElementById('md').value.trim();
  if(!md){msg('正文为空',false);return;}
  const body={title:document.getElementById('title').value.trim(),markdown:md,
    cover:document.getElementById('cover').value.trim(),
    tags:document.getElementById('tags').value.split(',').map(s=>s.trim()).filter(Boolean),
    platforms:plats};
  msg('同步中（多平台可能需数分钟）...');
  try{
    const d=await post('api/sync',body);
    if(d.error){msg(d.error,false);}else{msg('已提交');
      document.getElementById('rescard').style.display='block';
      document.getElementById('res').textContent=JSON.stringify(d.result??d,null,2);}
    setTimeout(refresh,2000);
  }catch(e){msg('同步失败 '+e,false);}
}
refresh();setInterval(refresh,10000);
</script></body></html>"""


class DashHandler(BaseHTTPRequestHandler):
    bridge = None
    cfg = None
    loop = None

    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/index.html"):
            data = DASH.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif p == "/api/status":
            self._json(200, load_state())
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        p = self.path.split("?")[0]
        ln = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(ln).decode() or "{}")
        if p == "/api/sync":
            async def run():
                art = {"title": (body.get("title") or "").strip(),
                       "markdown": body.get("markdown") or "",
                       "platforms": body.get("platforms") or [],
                       "cover": body.get("cover") or "",
                       "tags": body.get("tags") or [],
                       "retry": False}
                if not art["markdown"].strip():
                    return {"error": "正文为空"}
                if not art["platforms"]:
                    return {"error": "未选择平台"}
                art["title"] = art["title"] or extract_title(art["markdown"])
                if not art["title"]:
                    return {"error": "缺少标题"}
                md2, todo = replace_local_images(art["markdown"], BASE)
                art["markdown"] = md2
                art["todo_images"] = todo
                return await do_sync(self.bridge, art, self.cfg,
                                     log=self._log)
            try:
                fut = asyncio.run_coroutine_threadsafe(run(), self.loop)
                self._json(200, fut.result())
            except Exception as e:
                self._json(500, {"error": str(e)})
        elif p == "/api/check-auth":
            async def run2():
                try:
                    plats = await auth_snapshot(self.bridge, self.cfg)
                    save_state({"platforms": plats,
                                "platforms_ts": time.strftime("%Y-%m-%d %H:%M:%S")})
                    return {"ok": True}
                except Exception as e:
                    return {"ok": False, "error": str(e)}
            try:
                fut = asyncio.run_coroutine_threadsafe(run2(), self.loop)
                self._json(200, fut.result())
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
        else:
            self._json(404, {"error": "not found"})

    def _log(self, s):
        print("[%s] %s" % (time.strftime("%H:%M:%S"), s), flush=True)


async def monitor_loop(bridge, cfg):
    while True:
        await asyncio.sleep(cfg["monitor_interval_sec"])
        if not bridge.connected():
            continue
        try:
            plats = await auth_snapshot(bridge, cfg)
            save_state({"platforms": plats,
                        "platforms_ts": time.strftime("%Y-%m-%d %H:%M:%S")})
            n = sum(1 for p in plats.values() if p.get("authenticated"))
            print("[%s] 登录态监测: %d/%d 已登录"
                  % (time.strftime("%H:%M:%S"), n, len(plats)), flush=True)
        except Exception as e:
            print("[%s] 监测失败: %s" % (time.strftime("%H:%M:%S"), e), flush=True)


async def cmd_serve(args):
    cfg = load_config()
    if ws_serve is None:
        raise SystemExit("缺少依赖：pip3 install websockets")
    bridge = ExtBridge(cfg["token"])
    srv = await ws_serve(ws_handler_factory(bridge), "127.0.0.1", cfg["ws_port"])
    print("WS 服务: ws://127.0.0.1:%d  看板: http://127.0.0.1:%d"
          % (cfg["ws_port"], cfg["http_port"]), flush=True)

    loop = asyncio.get_running_loop()

    def http_ready():
        DashHandler.bridge = bridge
        DashHandler.cfg = cfg
        DashHandler.loop = loop
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", cfg["http_port"]), DashHandler)
        except OSError as e:
            raise SystemExit("看板端口 %d 不可用：%s" % (cfg["http_port"], e))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd

    httpd = await loop.run_in_executor(None, http_ready)
    mon = loop.create_task(monitor_loop(bridge, cfg))
    try:
        await asyncio.Future()
    except asyncio.CancelledError:
        pass
    finally:
        mon.cancel()
        httpd.shutdown()
        await close_server(srv)


async def cmd_platforms(args):
    cfg = load_config()
    if http_bridge_alive(cfg):
        import urllib.request
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/api/status" % cfg["http_port"], timeout=3) as r:
            st = json.loads(r.read().decode())
        plats = st.get("platforms") or {}
        for pid in PLATFORM_IDS:
            p = plats.get(pid, {})
            print("%-14s %-10s %s" % (pid, "已登录" if p.get("authenticated") else "未登录",
                                      p.get("error", "")))
        return
    bridge = ExtBridge(cfg["token"])
    srv = await run_ws_server(bridge, cfg["ws_port"], wait_sec=30)
    try:
        if not bridge.connected():
            raise SystemExit("扩展未连接")
        plats = await auth_snapshot(bridge, cfg)
        for pid in PLATFORM_IDS:
            p = plats.get(pid, {})
            print("%-14s %-10s %s" % (pid,
                                      "已登录" if p.get("authenticated") else "未登录",
                                      p.get("error", "")))
    finally:
        await close_server(srv)


def main():
    ap = argparse.ArgumentParser(description="多平台分发桥（CSDN同步助手 MCP 协议）")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("serve")
    sub.add_parser("platforms")
    for name, fn in (("sync", cmd_sync), ("sync-all", cmd_sync_all)):
        sp = sub.add_parser(name)
        sp.add_argument("file")
        sp.add_argument("--platform")
        sp.add_argument("--platforms")
        sp.add_argument("--title")
        sp.add_argument("--cover")
        sp.add_argument("--tags")
        sp.add_argument("--retry", action="store_true")
    a = ap.parse_args()
    if a.cmd == "serve":
        asyncio.run(cmd_serve(a))
    elif a.cmd == "platforms":
        asyncio.run(cmd_platforms(a))
    elif a.cmd == "sync":
        if not a.platform:
            raise SystemExit("sync 需要 --platform")
        asyncio.run(cmd_sync(a))
    elif a.cmd == "sync-all":
        asyncio.run(cmd_sync_all(a))
    else:
        ap.print_help()


async def cmd_sync_all(args):
    cfg = load_config()
    if args.platforms:
        ids = [x.strip() for x in args.platforms.split(",") if x.strip()]
    else:
        st = load_state().get("platforms") or {}
        ids = [pid for pid in PLATFORM_IDS if st.get(pid, {}).get("authenticated")]
        if not ids:
            import urllib.request
            if http_bridge_alive(cfg):
                with urllib.request.urlopen(
                        "http://127.0.0.1:%d/api/status" % cfg["http_port"],
                        timeout=3) as r:
                    st = json.loads(r.read().decode()).get("platforms") or {}
                ids = [pid for pid in PLATFORM_IDS if st.get(pid, {}).get("authenticated")]
    if not ids:
        raise SystemExit("没有已登录平台（先运行 serve 监测登录态，或用 --platforms 指定）")
    args.platform = None
    art = parse_article_file(args, ids)
    if http_bridge_alive(cfg):
        print("（走常驻桥）同步到 %s" % ",".join(ids))
        r = sync_via_http(cfg, art)
        print_sync_result(r)
        return
    print("常驻桥未运行，启动临时 WS 等待扩展连接...")
    bridge = ExtBridge(cfg["token"])
    srv = await run_ws_server(bridge, cfg["ws_port"], wait_sec=cfg["standalone_wait_sec"])
    try:
        if not bridge.connected():
            raise SystemExit("等待超时：扩展未连接")
        r = await do_sync(bridge, attach_local_images(art), cfg)
        print_sync_result(r)
    finally:
        await close_server(srv)


if __name__ == "__main__":
    main()
