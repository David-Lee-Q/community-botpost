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
import webbrowser
import uuid
from collections import deque
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
PLATFORMS_FILE = os.path.join(BASE, "platforms.json")
# 构建标记：每次发版更新（日期-短哈希），回传台账用于确认线上跑的版本
BRIDGE_BUILD = "20261116-r6"
# serve 启动时填充，供 WS 帧捕获推送台账使用
_CAPTURE_CFG = {}

DEFAULTS = {
    "ws_port": 3721,
    "http_port": 8390,
    "token": "",
    "monitor_interval_sec": 600,
    "request_timeout_sec": 300,
    "standalone_wait_sec": 90,
    "ledger_url": "",
    "ledger_poll_sec": 15,
    "ledger_cookie": "",
}

# 平台元数据（逆向自扩展 v1.2.1；isAiGraphic 的两个图文变体不支持 MCP）
DEFAULT_PLATFORMS = [
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


def _load_platforms():
    """优先读 platforms.json（与台账共享），缺文件时回退内置清单。"""
    try:
        with open(PLATFORMS_FILE, encoding="utf-8") as f:
            rows = json.load(f)
        out = []
        for r in rows:
            if isinstance(r, dict) and r.get("id"):
                out.append((r["id"], r.get("name", r["id"]),
                            r.get("url", "https://"), r.get("format", "markdown")))
        if out:
            return out
    except (OSError, ValueError):
        pass
    return list(DEFAULT_PLATFORMS)


PLATFORMS = _load_platforms()
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


_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def http_json(url, payload=None, timeout=60, cookie=""):
    """简单 HTTP JSON 调用（无第三方依赖，Windows 直接可用）。

    预览网关按登录态放行（匿名请求一律 400 请求错误）：
    带浏览器特征头 + 平台 Referer；cookie 非空时携带平台会话 Cookie。
    失败时把响应体并入异常，便于定位网关错误。"""
    import urllib.request
    import urllib.error
    from urllib.parse import urlparse
    data = None
    origin = "%s://%s" % (urlparse(url).scheme, urlparse(url).netloc)
    headers = {"User-Agent": _UA, "Accept": "application/json",
               "Accept-Language": "zh-CN,zh;q=0.9",
               "Referer": origin + "/",
               "Origin": origin,
               "Sec-Fetch-Site": "same-origin",
               "Sec-Fetch-Mode": "cors",
               "Sec-Fetch-Dest": "empty",
               "Upgrade-Insecure-Requests": "1"}
    if cookie:
        headers["Cookie"] = cookie
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            body = ""
        raise RuntimeError("HTTP %s %s" % (e.code, body or e.reason)) from e


_RELAY_CACHE = {"base": "", "ts": 0.0}
RELAY_TTL = 1800


def resolve_relay_base(stable_url, cookie=""):
    """稳定地址（/api/v1/p/...）只放行 GET，POST 会被网关 nginx 405。
    用 GET 触发其 302，取 Location 得到真正承载服务的 host（放 POST，匿名可达）。
    兼容 200 + JS/meta 跳转的形态。失败返回 None。"""
    import urllib.request
    import urllib.error
    import re
    from urllib.parse import urljoin

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(stable_url, headers={
        "User-Agent": _UA, "Accept": "text/html,application/json"})
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        with opener.open(req, timeout=20) as r:
            body = r.read(8192).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code not in (301, 302, 303, 307, 308):
            raise
        loc = (e.headers or {}).get("Location") or ""
        return urljoin(stable_url, loc) if loc else None
    m = re.search(r'(?:location\.href\s*=\s*|content="0;\s*url=)["\']?([^"\'\s>]+)', body)
    return urljoin(stable_url, m.group(1)) if m else None


def ledger_base(cfg, cookie=""):
    """台账请求的 base 地址。稳定地址自动解析为真实 host（缓存 30 分钟），
    非稳定地址（如本机直连或已填真实 host）原样返回。"""
    stable = (cfg.get("ledger_url") or "").rstrip("/")
    if not stable or "/api/v1/p/" not in stable:
        return stable
    now = time.time()
    if _RELAY_CACHE["base"] and now - _RELAY_CACHE["ts"] < RELAY_TTL:
        return _RELAY_CACHE["base"]
    try:
        got = resolve_relay_base(stable, cookie)
    except Exception:
        got = None
    if got:
        _RELAY_CACHE["base"] = got.rstrip("/")
        _RELAY_CACHE["ts"] = now
        return _RELAY_CACHE["base"]
    return _RELAY_CACHE["base"] or stable


def invalidate_relay_cache():
    _RELAY_CACHE["base"] = ""
    _RELAY_CACHE["ts"] = 0.0


async def _push_frame_to_ledger(base_url, rec, cookie=""):
    """把扩展主动推送的 WS 帧上报台账（排查异步同步结果形态用）。"""
    try:
        await asyncio.get_running_loop().run_in_executor(
            None, lambda: http_json(base_url + "/api/bridge-frame",
                                    {"ts": rec.get("ts", ""), "frame": rec.get("frame", {})},
                                    15, cookie=cookie))
    except Exception:
        pass


class ExtBridge:
    """与扩展的 WS 连接管理。"""

    def __init__(self, token=""):
        self.token = token
        self.conn = None
        self.pending = {}
        self.frames = deque(maxlen=300)
        self._frame_subs = {}

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
        self._capture_frame(msg, mid)
        fut = self.pending.pop(mid, None) if isinstance(mid, str) else None
        if fut is not None and not fut.done():
            fut.set_result(msg)

    def _capture_frame(self, msg, mid):
        """抓非心跳帧：请求响应帧只进环形缓冲；扩展主动推送的帧（无匹配请求 id）
        持久化到 state.ws_frames，并通知异步同步跟踪器（sync 结果回填的发现通道）。"""
        try:
            rec = {"ts": time.strftime("%H:%M:%S"),
                   "method": msg.get("method", ""),
                   "frame": msg}
            self.frames.append(rec)
            if isinstance(mid, str) and mid in self.pending:
                return
            try:
                st = load_state()
                frames = (st.get("ws_frames") or [])[-99:]
                frames.append(rec)
                save_state({"ws_frames": frames})
            except Exception:
                pass
            try:
                cfg = _CAPTURE_CFG
                if cfg.get("ledger_url"):
                    base = ledger_base(cfg, (cfg.get("ledger_cookie") or "").strip())
                    if base:
                        cookie = (cfg.get("ledger_cookie") or "").strip()
                        asyncio.get_running_loop().create_task(
                            _push_frame_to_ledger(base, rec, cookie))
            except Exception:
                pass
            s = json.dumps(msg, ensure_ascii=False)
            for sid, qs in list(self._frame_subs.items()):
                if sid and sid in s:
                    for q in qs:
                        try:
                            q.put_nowait(msg)
                        except Exception:
                            pass
        except Exception:
            pass

    def watch_frames(self, sync_id):
        q = asyncio.Queue()
        self._frame_subs.setdefault(sync_id, []).append(q)
        return q

    def unwatch_frames(self, sync_id, q=None):
        qs = self._frame_subs.get(sync_id)
        if qs:
            if q is not None and q in qs:
                qs.remove(q)
            else:
                qs.clear()
        if not self._frame_subs.get(sync_id):
            self._frame_subs.pop(sync_id, None)

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
    # 键必须覆盖全文（md 摘要），不能只取前 300 字：
    # 正文尾部变化（如追加原文链接）若不影响键，扩展会按
    # 「同键不同文章」拒绝。同键=同内容，幂等语义才成立。
    md_digest = hashlib.sha1(md.encode("utf-8", "replace")).hexdigest()
    raw = "|".join([title, md_digest, ",".join(sorted(platforms))])
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


ORIGINAL_URL = "https://openlab.cosmoplat.com/article-detils?id={aid}&articleType=0"


def with_original_link(md, article_id):
    """正文末尾追加卡奥斯开源社区原文链接（幂等：已含该链接则不重复加）。"""
    aid = str(article_id or "").strip()
    if not aid.isdigit():
        return md
    url = ORIGINAL_URL.format(aid=aid)
    if url in md:
        return md
    return md.rstrip() + "\n\n> 原文链接（卡奥斯开源社区）: %s" % url


async def do_sync(bridge, art, cfg, log=print):
    if art.get("article_id"):
        art["markdown"] = with_original_link(art["markdown"], art["article_id"])
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
    art["_sync_params"] = params
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


def _sync_entry_from_obj(e):
    """把扩展返回的单平台结果归一化为 {ok,url,error}。"""
    if not isinstance(e, dict):
        return {"ok": False, "url": "", "error": "未知结果格式"}
    ok = bool(e.get("success") or e.get("ok") or e.get("status") in ("success", "ok", "done"))
    err = e.get("error") or e.get("message") or ""
    if isinstance(err, dict):
        err = err.get("message", "")
    if e.get("error") or e.get("error_message"):
        ok = False
    url = (e.get("url") or e.get("draftUrl") or e.get("link")
           or e.get("draft_url") or e.get("editUrl") or e.get("draftLink")
           or e.get("articleUrl") or e.get("postUrl") or "")
    return {"ok": bool(ok), "url": str(url or "")[:500], "error": str(err or "")[:300]}


# 草稿编辑页链接模板（用户 2026-11-16 提供；{id} 用扩展返回的草稿 ID 填充，
# 无 {id} 占位的平台是固定入口页）
DRAFT_URL_TEMPLATES = {
    "juejin": "https://juejin.cn/editor/drafts/{id}",
    "baijiahao": "https://baijiahao.baidu.com/builder/rc/edit?type=news&article_id={id}&is_pay_training_camp=",
    "xiaohongshu": "https://creator.xiaohongshu.com/publish/publish?from=menu&target=article",
    "51cto": "https://blog.51cto.com/blogger/draft/{id}",
    "jianshu": "https://www.jianshu.com/writer#/notebooks/{nb}/notes/{id}",
    "segmentfault": "https://segmentfault.com/user/draft",
    "oschina": "https://my.oschina.net/u/7905287/blog/ai-write/draft/{id}",
    "tencentcloud": "https://cloud.tencent.com/developer/article/write-new?draftId={id}",
}
DRAFT_ID_KEYS = ("draftId", "draft_id", "draftID", "postId", "articleId",
                 "article_id", "noteId", "note_id", "blogId")


def extract_draft_id(entry):
    if not isinstance(entry, dict):
        return ""
    for k in DRAFT_ID_KEYS:
        v = entry.get(k)
        if v not in (None, "", 0):
            return str(v)
    v = entry.get("id")
    if v not in (None, "") and not isinstance(v, bool):
        return str(v)
    return ""


def build_draft_url(pid, entry, url):
    """草稿链接：扩展返回的编辑页 URL 优先；否则按模板+草稿 ID 拼直达链接；
    拼不出时保留原值（空 url 由看板回退到 platforms.json 的入口地址）。"""
    t = DRAFT_URL_TEMPLATES.get(pid)
    if not t:
        return url
    if "{id}" not in t:
        return t
    if url and re.search(r"(draft|edit|write|editor)", url, re.I):
        return url
    did = extract_draft_id(entry)
    if not did:
        return url
    if "{nb}" in t:
        nb = str((entry or {}).get("notebookId") or (entry or {}).get("notebook_id") or "")
        if not nb:
            return "https://www.jianshu.com/writer"
        t = t.replace("{nb}/", nb + "/")
    try:
        return t.format(id=did)[:500]
    except Exception:
        return url


def normalize_sync_result(r, platform_ids):
    """syncArticle 响应 -> ({platform_id: {ok,url,error}}, async_info)。

    扩展为异步任务模型：先回 {syncId, status:"preparing"}，逐平台草稿结果稍后产生。
    识别到该形态时 async_info 非空，由 track_sync_task 跟踪回填；
    其余形态按原有逻辑归一化（async_info 为 None）。"""
    out = {p: {"ok": False, "url": "", "error": ""} for p in platform_ids}
    if not isinstance(r, dict):
        return out, None
    if "error" in r:
        msg = str((r["error"] or {}).get("message", "同步失败"))[:300]
        for p in out:
            out[p]["error"] = msg
        return out, None
    res = r.get("result")
    async_info = None
    if isinstance(res, dict) and res:
        st = res.get("status")
        has_list = any(isinstance(res.get(k), list)
                       for k in ("platforms", "results", "items", "list", "synced"))
        if st in ("preparing", "syncing", "pending", "running", "queued", "processing"):
            async_info = {"syncId": str(res.get("syncId") or ""), "status": st}
        elif "syncId" in res and not has_list:
            async_info = {"syncId": str(res.get("syncId") or ""), "status": "unknown"}
    cands = []
    if isinstance(res, dict):
        for k in ("platforms", "results", "items", "list", "synced"):
            v = res.get(k)
            if isinstance(v, list):
                cands = v
                break
        if not cands:
            known = {p: res[p] for p in platform_ids if isinstance(res.get(p), dict)}
            if known:
                for p, v in known.items():
                    out[p] = _sync_entry_from_obj(v)
                    out[p]["url"] = build_draft_url(p, v, out[p]["url"])
                return out, None
    elif isinstance(res, list):
        cands = res
    for e in cands:
        if not isinstance(e, dict):
            continue
        pid = e.get("platform") or e.get("platformId") or e.get("id")
        if pid in out:
            out[pid] = _sync_entry_from_obj(e)
            out[pid]["url"] = build_draft_url(pid, e, out[pid]["url"])
    if cands and any(v["ok"] or v["url"] or v["error"] for v in out.values()):
        return out, None
    if async_info:
        note = "扩展异步生成草稿中（status=%s），完成后自动回填草稿链接" % async_info["status"]
        for p in out:
            out[p]["error"] = note
        return out, async_info
    if all(not v["ok"] and not v["error"] for v in out.values()) and isinstance(res, dict) and res:
        u = res.get("url") or res.get("draftUrl") or ""
        for p in out:
            out[p] = {"ok": True, "url": str(u or "")[:500], "error": ""}
    return out, None


async def push_auth_to_ledger(bridge, cfg, plats):
    """把登录态快照推给台账（失败只打日志，不影响主流程）。"""
    url = ledger_base(cfg, (cfg.get("ledger_cookie") or "").strip())
    if not url:
        return
    try:
        await asyncio.get_running_loop().run_in_executor(
            None, lambda: http_json(url + "/api/platform-auth",
                                    {"platforms": plats,
                                     "platforms_ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                                     "connected": bool(bridge.connected()),
                                     "build": BRIDGE_BUILD},
                                    cookie=cfg.get("ledger_cookie", "")))
    except Exception as e:
        print("[%s] 登录态推送台账失败: %s" % (time.strftime("%H:%M:%S"), e), flush=True)


async def _post_distribute_result(cfg, base_url, task_id, status, plats, raw=None):
    url = ledger_base(cfg, (cfg.get("ledger_cookie") or "").strip()) or base_url
    payload = {"task_id": task_id, "status": status, "platforms": plats}
    if raw is not None:
        try:
            payload["raw"] = json.dumps(raw, ensure_ascii=False)
        except Exception:
            payload["raw"] = str(raw)
    await asyncio.get_running_loop().run_in_executor(
        None, lambda: http_json(url + "/api/distribute-result", payload,
                                30, cookie=cfg.get("ledger_cookie", "")))


async def run_distribute_task(bridge, cfg, base_url, task):
    """执行一个台账下发的一键分发任务（目标=当前已登录平台）。"""
    t_id = task.get("task_id", "")
    log = lambda s: print("[ledger][%s] %s" % (t_id, s), flush=True)
    try:
        md = (task.get("content") or "").strip()
        title = (task.get("title") or "").strip() or extract_title(md)
        if not md:
            raise RuntimeError("任务正文为空")
        if not title:
            raise RuntimeError("任务缺少标题")
        st = load_state()
        auth = st.get("platforms") or {}
        targets = [p for p in PLATFORM_IDS if (auth.get(p) or {}).get("authenticated")]
        if not targets:
            plats = {p: {"ok": False, "url": "", "error": "当前无已登录平台（快照为空或未登录）"}
                     for p in PLATFORM_IDS}
            log("无已登录平台，任务标记失败")
            await _post_distribute_result(cfg, base_url, t_id, "failed", plats)
            return
        tags = [t for t in (task.get("tags") or []) if t]
        art = {"title": title, "markdown": md, "platforms": targets,
               "cover": task.get("cover") or "", "tags": tags, "retry": False,
               "article_id": str(task.get("article_id") or "")}
        md2, todo = replace_local_images(art["markdown"], "")
        art["markdown"] = md2
        art["todo_images"] = todo
        r = await do_sync(bridge, art, cfg, log=log)
        res, async_info = normalize_sync_result(r, targets)
        if async_info:
            status = "syncing"
            log("扩展异步受理（syncId=%s…），等待草稿生成，完成后自动回填链接"
                % async_info["syncId"][:24])
            save_state({"last_distribute": {"time": time.strftime("%Y-%m-%d %H:%M:%S"),
                                            "task_id": t_id, "status": status,
                                            "platforms": res,
                                            "raw": json.dumps(r, ensure_ascii=False)[:8000]}})
            await _post_distribute_result(cfg, base_url, t_id, "syncing", res, raw=r)
            asyncio.get_running_loop().create_task(
                track_sync_task(bridge, cfg, base_url, t_id, async_info["syncId"],
                                targets, requery_params=art.get("_sync_params"), log=log))
            return
        status = "done" if any(v["ok"] for v in res.values()) else "failed"
        log("完成: %s（成功 %d/%d）" % (status, sum(1 for v in res.values() if v["ok"]), len(targets)))
        save_state({"last_distribute": {"time": time.strftime("%Y-%m-%d %H:%M:%S"),
                                        "task_id": t_id, "status": status,
                                        "platforms": res,
                                        "raw": json.dumps(r, ensure_ascii=False)[:8000]}})
        await _post_distribute_result(cfg, base_url, t_id, status, res, raw=r)
    except Exception as e:
        log("任务执行失败: %s" % e)
        try:
            await _post_distribute_result(
                cfg, base_url, t_id, "failed",
                {p: {"ok": False, "url": "", "error": str(e)[:300]} for p in PLATFORM_IDS})
        except Exception as e2:
            log("回传失败结果也失败: %s" % e2)


TRACK_TIMEOUT_SEC = 600
TRACK_REQUERY_FIRST = 60
TRACK_REQUERY_EVERY = 30


async def track_sync_task(bridge, cfg, base_url, task_id, sync_id,
                          platform_ids, requery_params=None, log=print):
    """扩展异步受理后跟踪逐平台草稿结果并回填台账。

    两条获取通道：
      1) 扩展主动推送的 WS 帧（按 syncId 匹配，_capture_frame 通知）
      2) 60s 无推送后，用原幂等键重发 syncArticle（内容相同应命中去重，
         返回原任务结果而非重复建草稿）"""
    if not sync_id:
        return
    q = bridge.watch_frames(sync_id)
    log("异步同步已受理（syncId=%s…），开始跟踪草稿链接（上限 %d 分钟）"
        % (sync_id[:24], TRACK_TIMEOUT_SEC // 60))
    deadline = time.time() + TRACK_TIMEOUT_SEC
    next_requery = time.time() + TRACK_REQUERY_FIRST
    found = None
    try:
        while time.time() < deadline:
            frame = None
            try:
                frame = await asyncio.wait_for(q.get(), timeout=5)
            except asyncio.TimeoutError:
                pass
            if frame is not None:
                cand = frame if ("result" in frame or "error" in frame) else {"result": frame}
                norm, _a = normalize_sync_result(cand, platform_ids)
                if any(v["ok"] or v["url"] or v["error"] for v in norm.values()):
                    found = norm
                    log("收到扩展推送的逐平台同步结果，回填草稿链接")
                    break
            if requery_params and time.time() >= next_requery:
                next_requery = time.time() + TRACK_REQUERY_EVERY
                try:
                    r2 = await bridge.request("syncArticle", requery_params, timeout=30)
                    norm2, a2 = normalize_sync_result(r2, platform_ids)
                    if a2 is None and any(v["ok"] or v["url"] or v["error"] for v in norm2.values()):
                        found = norm2
                        log("幂等键复查命中逐平台结果，回填草稿链接")
                        break
                    why = (a2 or {}).get("status") or \
                        str(((r2.get("error") or {}).get("message")) or "")[:60]
                    log("幂等键复查：仍在等待（%s）" % why)
                except Exception as e:
                    log("幂等键复查异常: %s" % str(e)[:120])
    except Exception as e:
        log("跟踪异常退出: %s" % e)
    finally:
        bridge.unwatch_frames(sync_id, q)
    if found:
        status = "done" if any(v["ok"] for v in found.values()) else "failed"
        try:
            await _post_distribute_result(
                cfg, base_url, task_id, status, found,
                raw={"event": "track_backfill", "syncId": sync_id,
                     "time": time.strftime("%Y-%m-%d %H:%M:%S")})
            log("草稿链接回填完成: %s（%d/%d 带链接）"
                % (status, sum(1 for v in found.values() if v["url"]), len(found)))
        except Exception as e:
            log("回填结果上报台账失败: %s" % e)
    else:
        log("等待 %d 分钟仍未拿到逐平台结果（syncId=%s…），停止自动回填，可到各平台草稿箱查看"
            % (TRACK_TIMEOUT_SEC // 60, sync_id[:24]))


async def _ledger_set_status(bridge, cfg, ok, error=""):
    """把台账链路状态写进 state.json，供看板展示。"""
    base_url = (cfg.get("ledger_url") or "").rstrip("/")
    try:
        save_state({"ledger": {"url": base_url, "ok": bool(ok),
                               "last_poll_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                               "last_error": (error or "")[:300]}})
    except Exception:
        pass


async def ledger_loop(bridge, cfg):
    """轮询台账：拉取一键分发任务、响应登录态重查请求。

    稳定地址（/api/v1/p/...）只放行 GET、POST 被 nginx 405，
    这里先解析 302 拿到真实服务 host，轮询/回传都打真实 host。"""
    configured = (cfg.get("ledger_url") or "").rstrip("/")
    if not configured:
        print("[ledger] 未配置 ledger_url，台账联动关闭"
              "（在 config.json 设置 ledger_url 后重启即启用）", flush=True)
        return
    poll_sec = max(5, int(cfg.get("ledger_poll_sec", 15)))
    cookie = (cfg.get("ledger_cookie") or "").strip()
    base_url = ledger_base(cfg, cookie)
    if base_url != configured:
        print("[ledger] 已解析台账真实地址: %s" % base_url, flush=True)
    print("[ledger] 台账联动开启: %s（轮询 %ds，cookie %s）"
          % (base_url, poll_sec, "已配置" if cookie else "未配置"), flush=True)
    try:
        await asyncio.get_running_loop().run_in_executor(
            None, lambda: http_json(base_url + "/api/status",
                                    timeout=15, cookie=cookie))
        await _ledger_set_status(bridge, cfg, True)
        print("[ledger] 台账自检通过：链路正常", flush=True)
    except Exception as e:
        await _ledger_set_status(bridge, cfg, False, "自检失败: %s" % e)
        print("[ledger] 台账自检失败: %s" % e, flush=True)
        print("[ledger] 请检查 config.json 的 ledger_url 及本机到该地址的网络", flush=True)
    last_err = ""
    last_err_log = 0.0
    while True:
        try:
            if bridge.connected():
                data = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: http_json(base_url + "/api/distribute-poll",
                                            timeout=30, cookie=cookie))
                await _ledger_set_status(bridge, cfg, True)
                if data.get("recheck"):
                    await _check_and_save(bridge, cfg, tag="台账重查")
                task = data.get("task")
                if isinstance(task, dict) and task.get("task_id"):
                    print("[ledger] 收到分发任务 %s: %s"
                          % (task["task_id"], task.get("title", "")), flush=True)
                    asyncio.get_running_loop().create_task(
                        run_distribute_task(bridge, cfg, base_url, task))
            else:
                await _ledger_set_status(bridge, cfg, False, "扩展未连接，暂不轮询")
        except Exception as e:
            msg = "轮询失败: %s" % e
            now = time.time()
            if msg != last_err or now - last_err_log > 600:
                extra = ""
                if msg == last_err:
                    extra = "（重复错误，每 10 分钟提示一次）"
                if any(s in msg for s in ("404", "405", "410", "Name or service not known",
                                          "Connection refused", "ConnectionReset")):
                    invalidate_relay_cache()
                    try:
                        nb = ledger_base(cfg, cookie)
                        if nb != base_url:
                            base_url = nb
                            extra += " 台账地址已重新解析为 %s" % nb
                    except Exception:
                        pass
                print("[ledger] %s%s" % (msg, extra), flush=True)
                last_err, last_err_log = msg, now
            await _ledger_set_status(bridge, cfg, False, str(e))
        await asyncio.sleep(poll_sec)


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


async def run_sync(art, cfg):
    """执行一次同步：优先走常驻桥 HTTP，否则临时起 WS 等扩展连接。"""
    if ws_serve is None:
        raise SystemExit("缺少依赖：pip3 install websockets")
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


def resolve_platform_ids(args, cfg):
    """目标平台：优先 --platforms；否则取 state 中全部已登录（本地文件或常驻桥）。"""
    if getattr(args, "platforms", None):
        return [x.strip() for x in args.platforms.split(",") if x.strip()]
    st = load_state().get("platforms") or {}
    ids = [pid for pid in PLATFORM_IDS if st.get(pid, {}).get("authenticated")]
    if not ids and http_bridge_alive(cfg):
        import urllib.request
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/api/status" % cfg["http_port"],
                timeout=3) as r:
            st = json.loads(r.read().decode()).get("platforms") or {}
        ids = [pid for pid in PLATFORM_IDS if st.get(pid, {}).get("authenticated")]
    return ids


async def cmd_sync(args):
    cfg = load_config()
    art = parse_article_file(args, [args.platform])
    await run_sync(art, cfg)


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
  <span id="b-ledger" class="badge off">台账未连接</span>
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
      <input type="text" id="aid" placeholder="源文章ID（可选，正文末尾追加社区原文链接）" style="flex:1;min-width:220px">
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
    const lg=s.ledger||{}, lb=document.getElementById('b-ledger');
    if(!lg.url){lb.textContent='台账未配置';lb.className='badge';lb.title='config.json 未设置 ledger_url';}
    else if(lg.ok){lb.textContent='台账在线 '+(lg.last_poll_at||'').slice(11);lb.className='badge on';lb.title='台账链路正常，最近轮询 '+lg.last_poll_at;}
    else {lb.textContent='台账离线';lb.className='badge off';
      lb.title=(lg.last_error||'未知原因')
        +((lg.last_error||'').indexOf('请求错误')>=0
          ?'｜网关要求平台登录态：在 config.json 填 ledger_cookie（浏览器打开台账页→F12→Network→复制 Cookie 头）后重启'
          :'｜若桥是旧版代码：重新解压新 multiplatform.zip 覆盖后重启 serve');}
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
    article_id:document.getElementById('aid').value.trim(),
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

    def _cors(self):
        # 允许台账页（任意源）跨源直连本机桥
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        # 台账页是公网 HTTPS 站，直连本机 127.0.0.1 触发 Chrome Private Network Access，
        # 预检必须回这个头，否则浏览器直接 block 本地请求
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _json(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors()
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/index.html"):
            data = DASH.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self._cors()
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif p == "/api/status":
            self._json(200, load_state())
        elif p == "/api/frames":
            st = load_state()
            self._json(200, {"ws_frames": (st.get("ws_frames") or [])[-50:],
                             "ring": list(self.bridge.frames)[-30:]})
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
                       "article_id": str(body.get("article_id") or ""),
                       "task_id": str(body.get("task_id") or ""),
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
                r = await do_sync(self.bridge, art, self.cfg, log=self._log)
                norm, async_info = normalize_sync_result(r, art["platforms"])
                if async_info:
                    status = "syncing"
                    t_id = art.get("task_id") or ""
                    lb = ledger_base(self.cfg, (self.cfg.get("ledger_cookie") or "").strip()) \
                        if self.cfg.get("ledger_url") else ""
                    if t_id and lb:
                        try:
                            await _post_distribute_result(self.cfg, lb, t_id, "syncing",
                                                          norm, raw=r)
                        except Exception as e:
                            self._log("syncing 状态上报台账失败: %s" % e)
                        asyncio.get_running_loop().create_task(
                            track_sync_task(self.bridge, self.cfg, lb, t_id,
                                            async_info["syncId"], art["platforms"],
                                            requery_params=art.get("_sync_params"),
                                            log=lambda s: self._log("[track][%s] %s" % (t_id, s))))
                else:
                    status = "done" if any(v["ok"] for v in norm.values()) else "failed"
                try:
                    save_state({"last_http_sync": {
                        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "title": art["title"], "status": status,
                        "platforms": norm}})
                except Exception:
                    pass
                out = {"status": status, "platforms": norm, "raw": r}
                if async_info:
                    out["async"] = async_info
                return out
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
        elif p == "/api/open-drafts":
            urls = body.get("urls") or []
            if isinstance(urls, str):
                urls = [u.strip() for u in urls.split(",")]
            urls = [u for u in urls
                    if isinstance(u, str) and u.startswith(("http://", "https://"))][:20]
            opened = 0
            for u in urls:
                try:
                    webbrowser.open(u)
                    opened += 1
                    time.sleep(0.4)
                except Exception:
                    pass
            self._json(200, {"ok": True, "opened": opened, "requested": len(urls)})
        elif p == "/api/probe":
            try:
                fut = asyncio.run_coroutine_threadsafe(
                    probe_extension(self.bridge, self.cfg, tag="(手动)"), self.loop)
                out = fut.result(timeout=400)
                self._json(200, {"ok": True,
                                 "hits": [m for m, v in out.items() if v.get("ok")]})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
        else:
            self._json(404, {"error": "not found"})

    def _log(self, s):
        print("[%s] %s" % (time.strftime("%H:%M:%S"), s), flush=True)


PROBE_METHODS = [
    "tools/list", "listTools", "toolsList",
    "listDrafts", "getDrafts", "getDraft", "getDraftList", "listDraftList",
    "queryDrafts", "drafts", "draftList", "getPlatformDrafts",
    "listArticles", "getArticles", "listMyArticles", "myArticles", "getUserArticles",
    "syncStatus", "tasks", "getTask",
    "openUrl", "openTab", "open", "openPage", "navigate", "goto",
    "executeScript", "getContent", "screenshot", "getCookies", "getPlatformCookies",
    # 异步任务（syncId 前缀 mcp_task_）状态/结果查询候选
    "getMcpTask", "getMcpTaskStatus", "getMcpTaskResult", "mcpTask", "mcpTaskStatus",
    "getTaskStatus", "getTaskResult", "queryTask", "queryTaskStatus",
    "getSyncTask", "getSyncTaskStatus", "getSyncTaskResult", "getSyncResult",
    "syncResult", "listTasks", "listMcpTasks", "getSyncTasks", "syncTasks",
    "taskStatus", "getDraftUrl", "getDraftUrls", "getSyncDrafts",
]


async def probe_extension(bridge, cfg, tag=""):
    """探测扩展支持的方法（草稿箱/浏览器操作能力），结果落本机 state 并推送台账。"""
    out = {}
    for m in PROBE_METHODS:
        try:
            r = await bridge.request(m, {}, timeout=15)
            out[m] = {"ok": "error" not in r, "resp": json.dumps(r, ensure_ascii=False)[:1500]}
        except Exception as e:
            out[m] = {"ok": False, "error": str(e)[:200]}
    hits = [m for m, v in out.items() if v.get("ok")]
    save_state({"probe": {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "hits": hits}})
    print("[probe] 扩展能力探测完成%s: 命中 %s" % (tag, hits or "无"), flush=True)
    base = ledger_base(cfg, (cfg.get("ledger_cookie") or "").strip())
    if base:
        try:
            await asyncio.get_running_loop().run_in_executor(
                None, lambda: http_json(base + "/api/bridge-probe",
                                        {"time": time.strftime("%Y-%m-%d %H:%M:%S"),
                                         "results": out},
                                        30, cookie=cfg.get("ledger_cookie", "")))
            print("[probe] 结果已推送台账", flush=True)
        except Exception as e:
            print("[probe] 结果推送台账失败: %s" % e, flush=True)
    return out


async def _check_and_save(bridge, cfg, tag="监测"):
    try:
        plats = await auth_snapshot(bridge, cfg)
        save_state({"platforms": plats,
                    "platforms_ts": time.strftime("%Y-%m-%d %H:%M:%S")})
        n = sum(1 for p in plats.values() if p.get("authenticated"))
        print("[%s] 登录态%s: %d/%d 已登录"
              % (time.strftime("%H:%M:%S"), tag, n, len(plats)), flush=True)
        await push_auth_to_ledger(bridge, cfg, plats)
    except Exception as e:
        print("[%s] %s失败: %s" % (time.strftime("%H:%M:%S"), tag, e), flush=True)


async def monitor_loop(bridge, cfg):
    print("[等待扩展连接中...]（连上后立即做首次登录态检查）", flush=True)
    while not bridge.connected():
        await asyncio.sleep(5)
    await _check_and_save(bridge, cfg, tag="首查")
    asyncio.get_running_loop().create_task(probe_extension(bridge, cfg, tag="(首连)"))
    was_connected = True
    while True:
        await asyncio.sleep(cfg["monitor_interval_sec"])
        if not bridge.connected():
            was_connected = False
            continue
        await _check_and_save(bridge, cfg,
                              tag="重连首查" if not was_connected else "监测")
        was_connected = True


async def cmd_serve(args):
    """serve 入口：任何致命错误同时落盘 startup_error.log，避免控制台一闪而过丢失。"""
    try:
        await _cmd_serve_inner(args)
    except BaseException:
        import traceback
        try:
            with open(os.path.join(BASE, "startup_error.log"), "a", encoding="utf-8") as f:
                f.write("\n=== %s ===\n%s" % (
                    time.strftime("%Y-%m-%d %H:%M:%S"), traceback.format_exc()))
        except Exception:
            pass
        raise


async def _cmd_serve_inner(args):
    global _CAPTURE_CFG
    cfg = load_config()
    _CAPTURE_CFG = cfg
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
            raise SystemExit("看板端口 %d 不可用：%s（若刚重启过桥，等 30 秒再试；"
                              "或先执行 netstat -ano | findstr %d 查占用）"
                             % (cfg["http_port"], e, cfg["http_port"]))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd

    httpd = await loop.run_in_executor(None, http_ready)
    mon = loop.create_task(monitor_loop(bridge, cfg))
    ledger_task = None
    if (cfg.get("ledger_url") or "").strip():
        ledger_task = loop.create_task(ledger_loop(bridge, cfg))
    try:
        await asyncio.Future()
    except asyncio.CancelledError:
        pass
    finally:
        mon.cancel()
        if ledger_task is not None:
            ledger_task.cancel()
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
    sp = sub.add_parser("sync-bot", help="批量同步目录下全部/最近 N 篇 .md")
    sp.add_argument("dir", help="markdown 文章目录")
    sp.add_argument("--platforms", help="逗号分隔平台 id；缺省=全部已登录")
    sp.add_argument("--latest", type=int, help="只同步文件名排序后最近 N 篇")
    sp.add_argument("--cover", help="全局封面 URL（可选）")
    sp.add_argument("--tags", help="全局标签，逗号分隔（可选）")
    sp.add_argument("--retry", action="store_true")
    sp.add_argument("--delay", type=float, default=10, help="篇间隔秒数，默认 10")
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
    elif a.cmd == "sync-bot":
        asyncio.run(cmd_sync_bot(a))
    else:
        ap.print_help()


async def cmd_sync_all(args):
    cfg = load_config()
    ids = resolve_platform_ids(args, cfg)
    if not ids:
        raise SystemExit("没有已登录平台（先运行 serve 监测登录态，或用 --platforms 指定）")
    art = parse_article_file(args, ids)
    await run_sync(art, cfg)


async def cmd_sync_bot(args):
    """批量同步：把目录里全部（或最近 N 篇）.md 逐篇同步到目标平台。

    标题取各文件首行 # 标题；--cover/--tags 全局生效；篇间隔 --delay 秒。
    """
    import glob as _glob
    from types import SimpleNamespace
    cfg = load_config()
    ids = resolve_platform_ids(args, cfg)
    if not ids:
        raise SystemExit("没有已登录平台（先运行 serve 监测登录态，或用 --platforms 指定）")
    files = sorted(_glob.glob(os.path.join(args.dir, "*.md")))
    if args.latest:
        files = files[-args.latest:]
    if not files:
        raise SystemExit("目录里没有 .md 文件: %s" % args.dir)
    print("共 %d 篇待同步 -> %s" % (len(files), ",".join(
        NAME_BY_ID.get(p, p) for p in ids)))
    ok = 0
    for i, f in enumerate(files, 1):
        print("\n==== [%d/%d] %s ====" % (i, len(files), os.path.basename(f)), flush=True)
        a = SimpleNamespace(file=f, title=None, cover=args.cover,
                            tags=args.tags, retry=args.retry)
        try:
            art = parse_article_file(a, ids)
            await run_sync(art, cfg)
            ok += 1
        except (SystemExit, Exception) as e:
            print("本篇失败，继续: %s" % e, flush=True)
        if i < len(files):
            await asyncio.sleep(args.delay)
    print("\n完成: 成功 %d / 共 %d 篇" % (ok, len(files)))


if __name__ == "__main__":
    main()
