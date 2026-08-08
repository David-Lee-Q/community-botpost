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
        if _lock.locked():
            self._json(429, {"error": "已有文章正在生成，请稍候"})
            return
        _lock.acquire()
        threading.Thread(target=_run, args=(prompt,), daemon=True).start()
        self._json(200, {"state": "processing"})

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
        if self.path.startswith("/api/status"):
            try:
                with open(STATUS_FILE, encoding="utf-8") as f:
                    st = json.load(f)
            except (OSError, ValueError):
                st = {"state": "none"}
            self._json(200, st)
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
