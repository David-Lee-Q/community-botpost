import hashlib
import http.server
import json
import os
import re
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse

LEDGER = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(LEDGER)
STATUS_FILE = os.path.join(LEDGER, "data", "oneshot_status.json")
ONE_SHOT = os.path.join(ROOT, "bot", "one_shot.py")
OPTIMIZE = os.path.join(LEDGER, "optimize.py")
PLAN_FILE = os.path.join(ROOT, "bot", "plan.json")
REVIEWS_FILE = os.path.join(LEDGER, "data", "reviews.json")
SCORES_FILE = os.path.join(LEDGER, "data", "article_scores.json")
BOT_SCORE_FILE = os.path.join(LEDGER, "data", "bot_score.json")
BOT_POSTS_FILE = os.path.join(LEDGER, "data", "bot_posts.json")
DIST_TASKS_FILE = os.path.join(LEDGER, "data", "distribute_tasks.json")
PLATFORM_AUTH_FILE = os.path.join(LEDGER, "data", "platform_auth.json")
BODIES_FILE = os.path.join(LEDGER, "data", "article_bodies.json")

sys.path.insert(0, LEDGER)
sys.path.insert(0, os.path.join(ROOT, "bot"))
import sensitive  # noqa: E402
import optimize as opt  # noqa: E402

_lock = threading.Lock()
_refreshing = False
_summary_cache = {"t": 0.0, "body": None, "etag": ""}
SUMMARY_TTL = 30
DIST_LOCK = threading.Lock()
DIST_STALE_SEC = 600  # 运行中任务超过 10 分钟视为桥掉线，放回队列
DIST_KEEP = 100       # 任务文件最多保留条数


def _load_auth_token():
    tok = os.environ.get("AUTH_TOKEN", "")
    if not tok:
        try:
            with open(os.path.join(LEDGER, ".auth_token"), encoding="utf-8") as f:
                tok = f.read().strip()
        except OSError:
            tok = ""
    return tok


_AUTH_TOKEN = _load_auth_token()


def _read_json(path, fallback):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return fallback


def _write_json(path, obj):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _dist_load():
    return _read_json(DIST_TASKS_FILE, {})


def _dist_save(tasks):
    if len(tasks) > DIST_KEEP:
        keep = sorted(tasks, key=lambda k: tasks[k].get("created_at") or "",
                      reverse=True)[:DIST_KEEP]
        tasks = {k: tasks[k] for k in keep}
    _write_json(DIST_TASKS_FILE, tasks)


def _articles_list():
    return _read_json(os.path.join(LEDGER, "data", "articles.json"), {}).get("articles", [])


def _article_body(aid):
    """正文优先取本地缓存，缓存缺失时回源社区 API。"""
    bodies = _read_json(BODIES_FILE, {})
    b = bodies.get(str(aid))
    if isinstance(b, dict) and (b.get("content") or "").strip():
        return b
    art = opt._load_article(str(aid))
    return {"title": art.get("title", ""), "summary": art.get("summary", ""),
            "content": art.get("content", "")}


def _first_image(md):
    m = re.search(r"!\[[^\]]*\]\(([^)\s]+)", md or "")
    return m.group(1) if m else ""


def _platforms_meta():
    return _read_json(os.path.join(ROOT, "channels", "multiplatform", "platforms.json"), [])


def _build_summary():
    arts = _read_json(os.path.join(LEDGER, "data", "articles.json"), None)
    reviews = _read_json(REVIEWS_FILE, {})
    scores = _read_json(SCORES_FILE, {})
    bot_score = _read_json(BOT_SCORE_FILE, None)
    posts = _read_json(BOT_POSTS_FILE, {})
    reviews_light = {aid: {"score": r.get("score"), "grade": r.get("grade"),
                           "comment": r.get("comment", ""), "breakdown": r.get("breakdown")}
                     for aid, r in reviews.items()}
    hist_counts = {aid: len(h) for aid, h in scores.items() if isinstance(h, list)}
    payload = {
        "updatedAt": arts.get("updatedAt", "") if arts else "",
        "memberId": arts.get("memberId", "—") if arts else "—",
        "total": arts.get("total", 0) if arts else 0,
        "categories": arts.get("categories", {}) if arts else {},
        "articles": arts.get("articles", []) if arts else [],
        "reviews": reviews_light,
        "histCounts": hist_counts,
        "botScore": bot_score,
        "posts": posts,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return body, hashlib.md5(body).hexdigest()


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
        if _AUTH_TOKEN and self.headers.get("X-Auth-Token") != _AUTH_TOKEN:
            self._json(401, {"error": "需要操作口令（X-Auth-Token）"})
            return
        if self.path == "/api/plan-update":
            self._handle_plan_update()
            return
        if self.path == "/api/refresh":
            if _lock.locked():
                self._json(429, {"error": "已有任务执行中，请稍候"})
                return
            _lock.acquire()
            threading.Thread(target=_run_refresh, daemon=True).start()
            self._json(200, {"ok": True, "state": "refreshing"})
            return
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
        if self.path == "/api/publish-now":
            self._handle_publish_now()
            return
        if self.path == "/api/publish-batch":
            self._handle_publish_batch()
            return
        if self.path == "/api/distribute":
            self._handle_distribute()
            return
        if self.path == "/api/distribute-result":
            self._handle_distribute_result()
            return
        if self.path == "/api/distribute-poll":
            self._handle_distribute_poll()
            return
        if self.path == "/api/platform-auth":
            self._handle_platform_auth_push()
            return
        if self.path == "/api/platform-auth-recheck":
            self._handle_platform_auth_recheck()
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

    def _handle_publish_now(self):
        data = self._body()
        if not data or not data.get("taskId"):
            self._json(400, {"error": "参数缺失"})
            return
        tid = str(data["taskId"])
        try:
            with open(PLAN_FILE, encoding="utf-8") as f:
                plan = json.load(f)
        except (OSError, ValueError):
            self._json(500, {"error": "计划文件读取失败"})
            return
        item = next((it for it in plan.get("schedule", []) if it.get("taskId") == tid), None)
        if item is None:
            self._json(404, {"error": "计划不存在"})
            return
        if item.get("status") != "pending":
            self._json(200, {"ok": False, "skip": True,
                             "reason": "当前状态 " + str(item.get("status")) + "，不重复发布"})
            return
        if _lock.locked():
            self._json(429, {"error": "已有任务执行中，请稍候"})
            return
        _lock.acquire()
        threading.Thread(target=_run_publish_now, args=(tid,), daemon=True).start()
        self._json(200, {"ok": True, "state": "publishing"})

    def _handle_publish_batch(self):
        data = self._body() or {}
        try:
            count = int(data.get("count") or 2)
        except (TypeError, ValueError):
            count = 2
        count = max(1, min(count, 4))
        if _lock.locked():
            self._json(429, {"error": "已有任务执行中，请稍候"})
            return
        try:
            with open(PLAN_FILE, encoding="utf-8") as f:
                plan = json.load(f)
        except (OSError, ValueError):
            self._json(500, {"error": "计划文件读取失败"})
            return
        pend = [it for it in plan.get("schedule", []) if it.get("status") == "pending"]
        pend.sort(key=lambda x: x.get("time") or "")
        if not pend:
            self._json(400, {"error": "没有待发布的文章"})
            return
        targets = [it for it in pend[:count] if it.get("taskId")]
        if not targets:
            self._json(400, {"error": "待发布计划缺少 taskId"})
            return
        _lock.acquire()
        tids = [str(it["taskId"]) for it in targets]
        threading.Thread(target=_run_publish_batch, args=(tids,), daemon=True).start()
        self._json(200, {"ok": True, "state": "publishing",
                          "tasks": [{"taskId": it["taskId"], "title": it.get("title", ""),
                                     "time": it.get("time", "")} for it in targets]})

    def _handle_distribute(self):
        data = self._body() or {}
        aid = str(data.get("article_id") or "").strip()
        if not aid:
            self._json(400, {"error": "缺少 article_id"})
            return
        meta = next((a for a in _articles_list() if str(a.get("id")) == aid), None)
        if not meta:
            self._json(404, {"error": "文章不存在"})
            return
        if meta.get("status") != 1:
            self._json(400, {"error": "仅已发布文章可分发"})
            return
        with DIST_LOCK:
            tasks = _dist_load()
            for t in sorted(tasks.values(), key=lambda x: x.get("created_at") or ""):
                if str(t.get("article_id")) == aid and t.get("status") in ("pending", "running"):
                    self._json(200, {"ok": True, "task_id": t["task_id"],
                                     "status": t["status"], "existing": True,
                                     "created_at": t.get("created_at", "")})
                    return
            task_id = "d" + time.strftime("%Y%m%d%H%M%S") + os.urandom(2).hex()
            created = time.strftime("%Y-%m-%d %H:%M:%S")
            tasks[task_id] = {
                "task_id": task_id, "article_id": aid,
                "title": meta.get("title", ""), "status": "pending",
                "created_at": created,
                "claimed_at": "", "finished_at": "", "error": "", "platforms": {},
            }
            _dist_save(tasks)
            self._json(200, {"ok": True, "task_id": task_id, "status": "pending",
                             "created_at": created})

    def _handle_distribute_poll(self):
        """桥端轮询：领取一个待分发任务，顺带返回登录态重查标志。"""
        doc = _read_json(PLATFORM_AUTH_FILE, {})
        recheck = bool(doc.get("recheck_requested"))
        doc["last_poll_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        doc["last_poll_ts"] = int(time.time())
        task_out = None
        with DIST_LOCK:
            tasks = _dist_load()
            changed = False
            now_ts = time.time()
            for t in tasks.values():
                if t.get("status") == "running" and t.get("claimed_at"):
                    try:
                        ct = time.mktime(time.strptime(t["claimed_at"], "%Y-%m-%d %H:%M:%S"))
                    except ValueError:
                        ct = 0
                    if now_ts - ct > DIST_STALE_SEC:
                        t["status"] = "pending"
                        t["claimed_at"] = ""
                        changed = True
            for tid in sorted(tasks, key=lambda k: tasks[k].get("created_at") or ""):
                t = tasks[tid]
                if t.get("status") != "pending":
                    continue
                try:
                    body = _article_body(str(t["article_id"]))
                except Exception as e:
                    t["status"] = "failed"
                    t["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    t["error"] = "拉取正文失败: " + str(e)[:200]
                    changed = True
                    break
                t["status"] = "running"
                t["claimed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                changed = True
                meta = next((a for a in _articles_list()
                             if str(a.get("id")) == str(t["article_id"])), {})
                task_out = {
                    "task_id": tid,
                    "article_id": t["article_id"],
                    "title": (body.get("title") or t.get("title") or "").strip(),
                    "content": body.get("content", ""),
                    "cover": _first_image(body.get("content", "")),
                    "tags": [meta.get("cateName", "")] if meta.get("cateName") else [],
                }
                break
            if changed:
                _dist_save(tasks)
        doc["recheck_requested"] = False
        _write_json(PLATFORM_AUTH_FILE, doc)
        self._json(200, {"task": task_out, "recheck": recheck})

    def _handle_distribute_result(self):
        data = self._body() or {}
        tid = str(data.get("task_id") or "").strip()
        if not tid:
            self._json(400, {"error": "缺少 task_id"})
            return
        plats_in = data.get("platforms")
        if not isinstance(plats_in, dict):
            plats_in = {}
        with DIST_LOCK:
            tasks = _dist_load()
            t = tasks.get(tid)
            if not t:
                self._json(404, {"error": "任务不存在"})
                return
            norm = {}
            for pid, v in plats_in.items():
                if not isinstance(v, dict):
                    continue
                norm[str(pid)] = {"ok": bool(v.get("ok")),
                                  "url": str(v.get("url") or "")[:500],
                                  "error": str(v.get("error") or "")[:300]}
            t["platforms"] = norm
            t["status"] = str(data.get("status") or
                              ("done" if any(v["ok"] for v in norm.values()) else "failed"))
            t["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            t["raw"] = str(data.get("raw") or "")[:8000]
            _dist_save(tasks)
        self._json(200, {"ok": True, "status": t["status"]})

    def _handle_distribute_task(self):
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = {}
        for p in qs.split("&"):
            if "=" in p:
                k, v = p.split("=", 1)
                params[k] = urllib.parse.unquote(v)
        with DIST_LOCK:
            tasks = _dist_load()
        t = None
        if params.get("id"):
            t = tasks.get(params["id"])
        elif params.get("article_id"):
            cands = [x for x in tasks.values()
                     if str(x.get("article_id")) == params["article_id"]]
            cands.sort(key=lambda x: x.get("created_at") or "")
            t = cands[-1] if cands else None
        self._json(200, {"ok": t is not None, "task": t})

    def _handle_distribute_history(self):
        with DIST_LOCK:
            tasks = _dist_load()
        out = sorted(tasks.values(), key=lambda x: x.get("created_at") or "", reverse=True)
        self._json(200, {"tasks": out})

    def _handle_platform_auth_push(self):
        data = self._body() or {}
        plats = data.get("platforms")
        if not isinstance(plats, dict):
            self._json(400, {"error": "platforms 需为对象"})
            return
        doc = _read_json(PLATFORM_AUTH_FILE, {})
        doc["platforms"] = plats
        doc["platforms_ts"] = str(data.get("platforms_ts") or
                                  time.strftime("%Y-%m-%d %H:%M:%S"))
        doc["connected"] = bool(data.get("connected"))
        _write_json(PLATFORM_AUTH_FILE, doc)
        self._json(200, {"ok": True})

    def _handle_platform_auth_recheck(self):
        doc = _read_json(PLATFORM_AUTH_FILE, {})
        doc["recheck_requested"] = True
        _write_json(PLATFORM_AUTH_FILE, doc)
        self._json(200, {"ok": True})

    def _handle_platform_auth_get(self):
        doc = _read_json(PLATFORM_AUTH_FILE, {})
        self._json(200, {"platforms": doc.get("platforms", {}),
                         "platforms_ts": doc.get("platforms_ts", ""),
                         "connected": doc.get("connected", False),
                         "recheck": bool(doc.get("recheck_requested")),
                         "last_poll_at": doc.get("last_poll_at", ""),
                         "last_poll_ts": int(doc.get("last_poll_ts") or 0),
                         "meta": _platforms_meta()})

    def _handle_plan_update(self):
        data = self._body()
        tid = str((data or {}).get("taskId") or "").strip()
        if not tid:
            self._json(400, {"error": "参数缺失"})
            return
        if _lock.locked():
            self._json(429, {"error": "已有任务执行中，请稍候"})
            return
        try:
            with open(PLAN_FILE, encoding="utf-8") as f:
                plan = json.load(f)
        except (OSError, ValueError):
            self._json(500, {"error": "计划文件读取失败"})
            return
        item = next((it for it in plan.get("schedule", []) if it.get("taskId") == tid), None)
        if item is None:
            self._json(404, {"error": "计划不存在"})
            return
        if item.get("status") != "pending":
            self._json(200, {"ok": False, "skip": True, "reason": "仅待发布计划可编辑"})
            return
        title = str(data.get("title") or "").strip()
        if not title:
            self._json(400, {"error": "标题不能为空"})
            return
        if len(title) > 60:
            self._json(400, {"error": "标题过长（≤60字）"})
            return
        summary = str(data.get("summary") or "").strip()
        if len(summary) > 300:
            self._json(400, {"error": "摘要过长（≤300字）"})
            return
        time_str = str(data.get("time") or "").strip()
        if time_str and not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}(:\d{2})?", time_str):
            self._json(400, {"error": "计划时间格式应为 YYYY-MM-DD HH:MM"})
            return
        item["title"] = title
        item["summary"] = summary
        if time_str:
            item["time"] = time_str
        category = str(data.get("category") or "").strip()
        if category:
            item["category"] = category
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(PLAN_FILE), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)
        os.replace(tmp, PLAN_FILE)
        self._json(200, {"ok": True})

    def do_GET(self):
        if self.path.startswith("/api/commits"):
            commits = []
            try:
                out = subprocess.run(
                    ["git", "-C", ROOT, "log", "--pretty=format:%h|%ad|%s",
                     "--date=format:%Y-%m-%d %H:%M"],
                    capture_output=True, text=True, timeout=10,
                ).stdout
                for line in out.splitlines():
                    parts = line.split("|", 2)
                    if len(parts) == 3:
                        commits.append({"hash": parts[0], "date": parts[1], "message": parts[2]})
            except Exception:
                commits = []
            self._json(200, {"commits": commits, "total": len(commits)})
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
        if self.path.startswith("/api/summary"):
            now = time.time()
            if not _summary_cache["body"] or now - _summary_cache["t"] > SUMMARY_TTL:
                _summary_cache["body"], _summary_cache["etag"] = _build_summary()
                _summary_cache["t"] = now
            etag = _summary_cache["etag"]
            if self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                self.send_header("ETag", etag)
                self.end_headers()
                return
            body = _summary_cache["body"]
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("ETag", etag)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/api/refresh-status"):
            self._json(200, {"refreshing": _refreshing})
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
        if self.path.startswith("/api/distribute-poll"):
            self._handle_distribute_poll()
            return
        if self.path.startswith("/api/distribute-task"):
            self._handle_distribute_task()
            return
        if self.path.startswith("/api/distribute-history"):
            self._handle_distribute_history()
            return
        if self.path.startswith("/api/platform-auth"):
            self._handle_platform_auth_get()
            return
        super().do_GET()


def _run(prompt):
    try:
        subprocess.run([sys.executable, ONE_SHOT, prompt],
                       capture_output=True, timeout=900)
    finally:
        _lock.release()


def _run_publish_now(task_id):
    try:
        subprocess.run([sys.executable, os.path.join(ROOT, "bot", "publish.py"),
                        "--now", task_id],
                       capture_output=True, timeout=900)
    finally:
        _lock.release()


def _run_publish_batch(tids):
    for tid in tids:
        try:
            subprocess.run([sys.executable, os.path.join(ROOT, "bot", "publish.py"),
                            "--now", tid],
                           capture_output=True, timeout=900)
        except Exception:
            pass
    _lock.release()


def _run_refresh():
    global _refreshing
    _refreshing = True
    try:
        subprocess.run([sys.executable, os.path.join(LEDGER, "fetch_articles.py")],
                       capture_output=True, timeout=1800)
    finally:
        _refreshing = False
        _lock.release()


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8090"))
    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    httpd.serve_forever()
