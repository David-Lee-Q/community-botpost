import http.server
import json
import os
import socketserver
import subprocess
import sys
import threading

LEDGER = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(LEDGER)
STATUS_FILE = os.path.join(LEDGER, "data", "oneshot_status.json")
ONE_SHOT = os.path.join(ROOT, "bot", "one_shot.py")
OPTIMIZE = os.path.join(LEDGER, "optimize.py")
PLAN_FILE = os.path.join(ROOT, "bot", "plan.json")

sys.path.insert(0, LEDGER)
sys.path.insert(0, os.path.join(ROOT, "bot"))
import sensitive  # noqa: E402
import optimize as opt  # noqa: E402

_lock = threading.Lock()


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=LEDGER, **kwargs)

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path == "/api/check":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                self._json(400, {"error": "请求体非法"})
                return
            text = (data.get("text") or "").strip()
            hits = sensitive.check(text) if text else []
            self._json(200, {"hit": bool(hits), "words": hits})
            return
        if self.path == "/api/optimize":
            self._handle_optimize()
            return
        if self.path == "/api/update":
            self._handle_update()
            return
        if self.path != "/api/publish":
            self._json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._json(400, {"error": "请求体非法"})
            return
        prompt = (data.get("prompt") or "").strip()
        if not prompt:
            self._json(400, {"error": "请输入标题或关键词"})
            return
        if len(prompt) > 60:
            self._json(400, {"error": "标题或关键词过长（≤60字）"})
            return
        hits = sensitive.check(prompt)
        if hits:
            sensitive.log_record({
                "source": "发一篇输入", "article_title": prompt,
                "hits": hits, "action": "拒绝（输入含敏感词）",
            })
            self._json(400, {"error": "输入内容包含敏感词：" + "、".join(hits)})
            return
        if _lock.locked():
            self._json(429, {"error": "已有文章正在生成，请稍候"})
            return
        _lock.acquire()
        threading.Thread(target=_run, args=(prompt,), daemon=True).start()
        self._json(200, {"state": "processing"})

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return None

    def _handle_optimize(self):
        data = self._body()
        if not data or not data.get("id"):
            self._json(400, {"error": "参数缺失"})
            return
        try:
            if data.get("content"):
                article = {
                    "title": data.get("title", ""), "summary": data.get("summary", ""),
                    "content": data.get("content", ""),
                }
            else:
                article = opt._load_article(data["id"])
            result = opt.ai_optimize(article)
            self._json(200, {"ok": True, "result": result})
        except Exception as e:
            self._json(200, {"ok": False, "error": str(e)[:300]})

    def _handle_update(self):
        data = self._body()
        if not data or not data.get("id") or not data.get("title") or not data.get("content"):
            self._json(400, {"error": "标题与正文不能为空"})
            return
        if _lock.locked():
            self._json(429, {"error": "已有文章正在生成/更新，请稍候"})
            return
        _lock.acquire()
        try:
            result = opt.update_and_review(
                str(data["id"]), str(data["title"]).strip(),
                str(data.get("summary", "")).strip(), str(data["content"]).strip())
            self._json(200, {"ok": True, "result": result})
        except Exception as e:
            self._json(200, {"ok": False, "error": str(e)[:300]})
        finally:
            _lock.release()

    def do_GET(self):
        if self.path.startswith("/api/commits"):
            commits = []
            try:
                out = subprocess.run(
                    ["git", "-C", ROOT, "log", "--pretty=format:%h|%ad|%s",
                     "--date=format:%Y-%m-%d %H:%M", "-n", "60"],
                    capture_output=True, text=True, timeout=10,
                ).stdout
                for line in out.splitlines():
                    parts = line.split("|", 2)
                    if len(parts) == 3:
                        commits.append({"hash": parts[0], "date": parts[1], "message": parts[2]})
            except Exception:
                commits = []
            self._json(200, {"commits": commits})
            return
        if self.path.startswith("/api/article"):
            try:
                aid = self.path.split("?id=")[1].split("&")[0]
                self._json(200, {"ok": True, "article": opt._load_article(aid)})
            except Exception as e:
                self._json(200, {"ok": False, "error": str(e)[:300]})
            return
        if self.path.startswith("/api/scores"):
            try:
                aid = self.path.split("?id=")[1].split("&")[0]
                self._json(200, {"ok": True, "history": opt.get_history(aid)})
            except Exception as e:
                self._json(200, {"ok": False, "error": str(e)[:300]})
            return
        if self.path.startswith("/api/status"):
            try:
                with open(STATUS_FILE, encoding="utf-8") as f:
                    st = json.load(f)
            except (OSError, ValueError):
                st = {"state": "none"}
            self._json(200, st)
            return
        if self.path.startswith("/api/plan"):
            try:
                with open(PLAN_FILE, encoding="utf-8") as f:
                    plan = json.load(f)
                self._json(200, {"ok": True, "schedule": plan.get("schedule", [])})
            except (OSError, ValueError) as e:
                self._json(200, {"ok": False, "error": str(e)[:300]})
            return
        super().do_GET()


def _run(prompt):
    try:
        subprocess.run([sys.executable, ONE_SHOT, prompt],
                       capture_output=True, timeout=600)
    finally:
        _lock.release()


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8090"))
    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    httpd.serve_forever()
