"""小黑线稿插图（文生图 MCP）——每日发文优先配图源。

- MCP 配置：环境变量 T2I_MCP_URL / T2I_MCP_TOKEN，或 bot/t2i_mcp.json（不入 git）
- illustrate(title, summary, body) -> {"body_files": [jpg1, jpg2], "cover": jpg, "files": [...]}
  内部：LLM 选 2 个认知锚点 -> 逐张调用 generate_image（1792x1024 / sketch / b64_json）-> 压缩
- 任一步失败抛异常，由调用方回退到图库
"""
import io
import json
import os
import queue
import re
import threading
import time

import requests
from PIL import Image

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
CFG_FILE = os.path.join(BOT_DIR, "t2i_mcp.json")
ILLU_DIR = os.path.join(BOT_DIR, "illustrations")
SIZE = "1792x1024"
STYLE = "sketch"


def _config():
    url = os.environ.get("T2I_MCP_URL", "").strip()
    tok = os.environ.get("T2I_MCP_TOKEN", "").strip()
    if not (url and tok):
        try:
            with open(CFG_FILE, encoding="utf-8") as f:
                d = json.load(f)
            url = url or d.get("url", "")
            tok = tok or d.get("token", "")
        except (OSError, ValueError):
            pass
    return url, tok


class _Client:
    def __init__(self, url, token):
        self.url = url
        self.auth = {"Authorization": "Bearer " + token}
        self.q = queue.Queue()
        self.endpoint = {}
        self._resp = None
        self._t = threading.Thread(target=self._read, daemon=True)

    def _read(self):
        try:
            self._resp = requests.get(self.url, headers={**self.auth, "Accept": "text/event-stream"},
                                      stream=True, timeout=(10, 600))
            event = None
            for raw in self._resp.iter_lines(decode_unicode=False):
                if raw is None:
                    continue
                line = raw.decode("utf-8", "replace")
                if line.startswith("event:"):
                    event = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    data = line.split(":", 1)[1].strip()
                    if event == "endpoint":
                        self.endpoint["path"] = data
                    self.q.put(data)
                elif line == "":
                    event = None
        except Exception:
            pass

    def start(self):
        self._t.start()
        for _ in range(80):
            if "path" in self.endpoint:
                return
            time.sleep(0.2)
        raise RuntimeError("MCP SSE 未返回 endpoint")

    def _msg_url(self):
        base = self.url.rsplit("/sse", 1)[0]
        p = self.endpoint["path"]
        return base + p if p.startswith("/") else p

    def _post(self, obj):
        r = requests.post(self._msg_url(), headers={**self.auth, "Content-Type": "application/json"},
                          json=obj, timeout=30)
        if r.status_code >= 400:
            raise RuntimeError("MCP POST %s" % r.status_code)

    def _wait(self, want, sec):
        end = time.time() + sec
        while time.time() < end:
            try:
                data = self.q.get(timeout=1)
            except queue.Empty:
                continue
            try:
                m = json.loads(data)
            except ValueError:
                continue
            if m.get("id") == want:
                return m
        raise RuntimeError("MCP 响应超时(id=%s)" % want)

    def initialize(self):
        self._post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                               "clientInfo": {"name": "bot-illustration", "version": "1.0"}}})
        self._wait(1, 15)
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    def generate(self, prompt, idx, timeout=300):
        self._post({"jsonrpc": "2.0", "id": idx, "method": "tools/call", "params": {
            "name": "generate_image", "arguments": {
                "prompt": prompt, "n": 1, "size": SIZE, "style": STYLE,
                "response_format": "b64_json", "translate": False}}})
        res = self._wait(idx, timeout)
        if res.get("result", {}).get("isError"):
            raise RuntimeError("生图失败: " + str(res.get("result", {}).get("content"))[:200])
        txt = res["result"]["content"][0]["text"]
        i = txt.rfind("base64 数据）:")
        m = re.match(r"[A-Za-z0-9+/=\s]+", txt[i + len("base64 数据）:"):]) if i >= 0 else None
        if not m:
            raise RuntimeError("返回中未找到 base64")
        import base64
        return base64.b64decode(re.sub(r"\s", "", m.group(0)))

    def close(self):
        """SSE 连接关闭可能阻塞读取，放到守护线程里做，避免拖住发布流程。"""
        r = self._resp
        if r is None:
            return

        def _shut():
            try:
                r.raw.close()
            except Exception:
                pass
            try:
                r.close()
            except Exception:
                pass

        threading.Thread(target=_shut, daemon=True).start()


def _parse_shots(text):
    s = (text or "").strip()
    if "```" in s:
        s = re.sub(r"```[a-zA-Z]*", "", s).replace("```", "")
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        raise ValueError("未找到 JSON 对象")
    data = json.loads(s[i:j + 1])
    shots = [x for x in (data.get("shots") or [])
             if isinstance(x, dict) and str(x.get("composition") or "").strip()]
    if len(shots) < 2:
        raise ValueError("有效 shot 不足 2 个")
    return shots[:2]


def _generic_shots(title, summary):
    core = (summary or title)[:60]
    return [
        {"topic": title[:12], "structure": "核心判断",
         "core": core,
         "composition": "画面：小黑站在左侧杂乱的数据和右侧清晰结论之间，用一条粗箭头把问题推过一道判断关口。",
         "labels": ["现状", "判断", "结论"]},
        {"topic": "落地路径", "structure": "承接路径",
         "core": core,
         "composition": "画面：小黑把三张步骤卡片依次放上一条时间线，末尾箭头指向一个可执行的结果。",
         "labels": ["第一步", "第二步", "结果"]},
    ]


def _shots(title, summary, body):
    import sensitive
    sys_prompt = (
        "你是中文技术文章的配图策划。阅读文章，选出 2 个最适合画成‘小黑怪诞手绘解释图’的认知锚点"
        "（核心判断、两个断点、输入输出闭环、分流、前后对比、承接路径等）。"
        "只输出 JSON，不要 markdown 代码块、不要解释：{\"shots\":[{\"topic\":\"≤12字\","
        "\"structure\":\"结构类型\",\"core\":\"一句核心意思\",\"composition\":\"画面，≤50字\","
        "\"labels\":[\"3-4个短词\"]}]}。"
    )
    user = "标题：%s\n摘要：%s\n正文：\n%s" % (title, summary, body[:2200])
    last = None
    for attempt, (temp, mt) in enumerate(((0.4, 2000), (0.2, 1800)), 1):
        try:
            text = sensitive.chat([
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user},
            ], max_tokens=mt, temperature=temp, timeout=60)
            return _parse_shots(text)
        except Exception as e:
            last = e
            _log_fail(title, "shots%d" % attempt, e)
    _log_fail(title, "shots-fallback", last or RuntimeError("shots 失败"))
    return _generic_shots(title, summary)


def _prompt(shot):
    labels = " / ".join(shot.get("labels") or []) or "关键概念"
    return (
        "16:9 horizontal Chinese article illustration. Pure white background, minimalist black hand-drawn "
        "line art, slightly wobbly pen, lots of empty white space. 小黑 is a small solid-black creature with "
        "white dot eyes, thin legs, blank serious face, and must perform the core conceptual action, not decorate. "
        "Theme: %s. Structure: %s. Core idea: %s. Composition: %s. "
        "Sparse red/orange/blue handwritten Chinese labels: %s. Black line art and 小黑; orange for main flow/arrows; "
        "red only for key problems/results; blue only for secondary notes. One image explains only one structure, "
        "subject 40-60%% of canvas, keep 35%%+ blank. Clean absurd product-sketch feeling. "
        "No title in top-left corner, no structure-type text, no PPT infographic, no gradients, no shadows, not cute, "
        "do not copy prior examples."
        % (shot.get("topic", ""), shot.get("structure", ""), shot.get("core", ""),
           shot.get("composition", ""), labels))


def _to_jpeg(png_bytes, max_w=1400, quality=82):
    im = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    if im.width > max_w:
        im = im.resize((max_w, int(im.height * max_w / im.width)), Image.LANCZOS)
    buf = io.BytesIO()
    q = quality
    im.save(buf, "JPEG", quality=q, optimize=True)
    while buf.tell() > 880 * 1024 and q > 50:
        q -= 8
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=q, optimize=True)
    return buf.getvalue()


def _cover(png_bytes, out):
    im = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    r = 2.0
    w, h = im.size
    if w / h > r:
        nw = int(h * r)
        left = (w - nw) // 2
        box = (left, 0, left + nw, h)
    else:
        nh = int(w / r)
        top = (h - nh) // 2
        box = (0, top, w, top + nh)
    im.crop(box).resize((800, 400), Image.LANCZOS).save(out, "JPEG", quality=85, optimize=True)
    return out


ERR_LOG = os.path.join(BOT_DIR, "illustration_errors.log")


def _log_fail(title, stage, exc):
    import traceback
    try:
        with open(ERR_LOG, "a", encoding="utf-8") as f:
            f.write("[%s] %s | %s | %s\n%s\n" % (
                time.strftime("%Y-%m-%d %H:%M:%S"), stage, title[:40],
                type(exc).__name__ + ": " + str(exc)[:300],
                traceback.format_exc()[-1500:]))
    except OSError:
        pass


def illustrate(title, summary, body, outdir=None, prefix="ill",
               img_timeout=300, budget=None):
    url, token = _config()
    if not (url and token):
        raise RuntimeError("未配置 T2I MCP")
    start = time.time()
    try:
        shots = _shots(title, summary, body)
    except Exception as e:
        _log_fail(title, "shots", e)
        raise
    outdir = outdir or os.path.join(ILLU_DIR, time.strftime("%Y%m%d"))
    os.makedirs(outdir, exist_ok=True)

    last = None
    for attempt in (1, 2):
        client = _Client(url, token)
        try:
            client.start()
            client.initialize()
            pngs, files = [], []
            for k, shot in enumerate(shots, 1):
                if budget and time.time() - start > budget:
                    raise RuntimeError("插图超出时间预算")
                png = client.generate(_prompt(shot), idx=100 + k, timeout=img_timeout)
                pngs.append(png)
                body_path = os.path.join(outdir, "%s-%d.jpg" % (prefix, k))
                with open(body_path, "wb") as f:
                    f.write(_to_jpeg(png))
                files.append(body_path)
            cover = _cover(pngs[0], os.path.join(outdir, "%s-cover.jpg" % prefix))
            return {"body_files": files, "cover": cover, "files": files + [cover]}
        except Exception as e:
            last = e
            _log_fail(title, "attempt%d" % attempt, e)
            elapsed = time.time() - start
            fast_fail = elapsed < 45
            room = (not budget) or (budget - elapsed > 120)
            if attempt == 1 and fast_fail and room:
                time.sleep(2)
                continue
            raise
        finally:
            client.close()
    raise last
