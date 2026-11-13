"""CSDN 分发队列（独立进程，只读 bot/plan.json，不影响原渠道）。

用法：
  python3 csdn_runner.py --dry-run          打印待分发清单，不执行
  python3 csdn_runner.py --once             跑一轮
  python3 csdn_runner.py --loop             常驻，每 60 秒一轮
  python3 csdn_runner.py --backfill 7       指定回填天数（默认取 config）
"""
import argparse
import datetime as dt
import fcntl
import json
import os
import subprocess
import sys
import tempfile
import time

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(BASE))
PLAN = os.path.join(ROOT, "bot", "plan.json")
STATE_FILE = os.path.join(BASE, "state.json")
QUEUE_FILE = os.path.join(BASE, "queue.json")
CONFIG_FILE = os.path.join(BASE, "config.json")
LOGS = os.path.join(BASE, "logs")
LOCK_FILE = os.path.join(BASE, ".lock")
ALERT_FILE = os.path.join(LOGS, "alerts.json")
PUBLISHER = os.path.join(BASE, "csdn_publisher.py")


def log(msg):
    os.makedirs(LOGS, exist_ok=True)
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    with open(os.path.join(LOGS, "csdn.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def alert(kind, detail):
    data = load_json(ALERT_FILE, [])
    data.append({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "type": kind, "detail": detail})
    save_json(ALERT_FILE, data[-200:])


def acquire_lock():
    os.makedirs(BASE, exist_ok=True)
    fh = open(LOCK_FILE, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def published_items(days):
    plan = load_json(PLAN, {})
    cutoff = (dt.datetime.now() - dt.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    items = []
    for it in plan.get("schedule", []):
        if it.get("status") != "published" or not it.get("article_id"):
            continue
        if (it.get("time") or "") >= cutoff:
            items.append(it)
    items.sort(key=lambda x: x.get("time") or "")
    return items


def enqueue(items, state, queue, today):
    known = {j["article_id"] for j in queue.get("jobs", [])}
    known |= {aid for aid, v in (state.get("articles") or {}).items()
              if v.get("status") in ("success", "failed")}
    added = 0
    for it in items:
        aid = str(it["article_id"])
        if aid in known or not it.get("file"):
            continue
        imgs = it.get("body_image_files") or []
        queue.setdefault("jobs", []).append({
            "article_id": aid, "title": it.get("title", ""),
            "file": it["file"], "cover": imgs[0] if imgs else it.get("cover", ""),
            "body_image_files": imgs,
            "category": it.get("category", ""),
            "time": it.get("time", ""), "attempts": 0, "next_try_at": "",
            "backfill": (it.get("time") or "") < today + " 00:00:00",
        })
        added += 1
    return added


def _daily(state, day):
    d = state.setdefault("days", {}).setdefault(day, {"total": 0, "backfill": 0})
    d.setdefault("total", 0)
    d.setdefault("backfill", 0)
    return d


def _in_window(cfg, now):
    win = cfg.get("publish_window") or ["08:00", "20:00"]
    try:
        start, end = win[0], win[1]
    except (TypeError, IndexError):
        return True
    t = now.strftime("%H:%M")
    return start <= t <= end


def can_run(job, cfg, state, now):
    if state.get("quota_blocked_date") == now.strftime("%Y-%m-%d"):
        return False, "今日发文上限"
    if not _in_window(cfg, now):
        return False, "不在发布时段"
    if job.get("next_try_at") and job["next_try_at"] > now.strftime("%Y-%m-%d %H:%M:%S"):
        return False, "等待重试"
    last = state.get("last_publish") or ""
    if last:
        try:
            gap = (dt.datetime.now() - dt.datetime.strptime(last, "%Y-%m-%d %H:%M:%S")).total_seconds()
        except ValueError:
            gap = 10 ** 6
        if gap < int(cfg.get("min_interval_sec", 180)):
            return False, "限速 %ds" % (int(cfg.get("min_interval_sec", 180)) - gap)
    day = _daily(state, now.strftime("%Y-%m-%d"))
    if day["total"] >= int(cfg.get("daily_limit", 20)):
        return False, "当日总量已达上限"
    if job.get("backfill") and day["backfill"] >= int(cfg.get("backfill_daily_limit", 12)):
        return False, "回填额度已用完"
    return True, ""


def run_job(job, cfg):
    jobs_dir = os.path.join(LOGS, "jobs")
    os.makedirs(jobs_dir, exist_ok=True)
    path = os.path.join(jobs_dir, str(job["article_id"]) + ".json")
    save_json(path, job)
    cmd = [sys.executable, PUBLISHER, "--job", path]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    out = (proc.stdout or "").strip().splitlines()
    try:
        return json.loads(out[-1]) if out else {"ok": False, "error": "无输出"}
    except ValueError:
        return {"ok": False, "error": (proc.stderr or "输出解析失败")[-300:]}


def process(state, queue, cfg):
    now = dt.datetime.now()
    now_s = now.strftime("%Y-%m-%d %H:%M:%S")
    today = now.strftime("%Y-%m-%d")
    handled = 0
    jobs = sorted(list(queue.get("jobs", [])),
                  key=lambda j: 0 if j.get("priority") else 1)
    for job in jobs:
        ok, why = can_run(job, cfg, state, now)
        if not ok:
            continue
        log("开始分发 %s %s%s" % (job["article_id"], job.get("title", "")[:24],
                                  "（置顶）" if job.get("priority") else ""))
        job.pop("priority", None)
        res = run_job(job, cfg)
        day = _daily(state, today)
        day["total"] += 1
        if job.get("backfill"):
            day["backfill"] += 1
        aid = str(job["article_id"])
        if res.get("ok"):
            state.setdefault("articles", {})[aid] = {
                "status": "success", "url": res.get("url", ""), "time": now_s,
                "attempts": job.get("attempts", 0) + 1,
                "title": job.get("title", ""),
            }
            queue["jobs"].remove(job)
            state["last_publish"] = now_s
            state["session"]["needs_login"] = False
            log("分发成功 %s %s" % (aid, res.get("url", "")))
        else:
            job["attempts"] = job.get("attempts", 0) + 1
            err = res.get("error", "")
            if "登录" in err:
                state["session"]["needs_login"] = True
                alert("needs_login", {"article_id": aid, "error": err})
                log("登录态失效，暂停队列")
                break
            if "发文上限" in err:
                state["quota_blocked_date"] = today
                job["next_try_at"] = ""
                job["attempts"] = 0
                alert("quota", {"article_id": aid, "error": err})
                log("CSDN 今日发文已达上限，暂停到明日")
                break
            if job["attempts"] >= int(cfg.get("max_retries", 3)):
                state.setdefault("articles", {})[aid] = {
                    "status": "failed", "error": err, "time": now_s,
                    "attempts": job["attempts"], "title": job.get("title", ""),
                }
                queue["jobs"].remove(job)
                alert("failed", {"article_id": aid, "title": job.get("title", ""), "error": err})
                log("分发失败（已放弃） %s %s" % (aid, err[:120]))
            else:
                mins = (cfg.get("retry_minutes") or [5, 15, 60])[job["attempts"] - 1]
                job["next_try_at"] = (now + dt.timedelta(minutes=mins)).strftime("%Y-%m-%d %H:%M:%S")
                alert("retry", {"article_id": aid, "error": err, "attempts": job["attempts"]})
                log("分发失败，%d 分钟后重试 %s %s" % (mins, aid, err[:120]))
        handled += 1
        if state["session"].get("needs_login"):
            break
    counters = {"pending": len(queue.get("jobs", [])),
                "success": sum(1 for v in state.get("articles", {}).values() if v.get("status") == "success"),
                "failed": sum(1 for v in state.get("articles", {}).values() if v.get("status") == "failed")}
    state["counters"] = counters
    return handled


def one_round(cfg, days, dry_run=False):
    state = load_json(STATE_FILE, {})
    state.setdefault("articles", {})
    state.setdefault("session", {"needs_login": False})
    queue = load_json(QUEUE_FILE, {"jobs": []})
    queue.setdefault("jobs", [])
    today = dt.datetime.now().strftime("%Y-%m-%d")

    items = published_items(days)
    added = enqueue(items, state, queue, today)
    if not dry_run:
        save_json(QUEUE_FILE, queue)
        save_json(STATE_FILE, state)
    if added:
        log("新增分发任务 %d 条（候选 %d）" % (added, len(items)))

    if dry_run:
        rows = [{"article_id": j["article_id"], "time": j.get("time"),
                 "backfill": j.get("backfill"), "attempts": j.get("attempts", 0),
                 "title": j.get("title")} for j in queue["jobs"]]
        rows.sort(key=lambda x: x.get("time") or "")
        print(json.dumps({"candidates": len(items), "queued": len(rows),
                          "days": days, "jobs": rows,
                          "counters": state.get("counters", {})},
                         ensure_ascii=False, indent=2))
        return 0

    if not cfg.get("enabled"):
        log("分发通道未启用（config.enabled=false）")
        return 0
    if state["session"].get("needs_login"):
        log("需要人工登录，跳过本轮")
        return 0
    if not queue["jobs"]:
        return 0
    n = process(state, queue, cfg)
    save_json(QUEUE_FILE, queue)
    save_json(STATE_FILE, state)
    log("本轮处理 %d 条，队列剩余 %d" % (n, len(queue["jobs"])))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--backfill", type=int, default=0)
    args = ap.parse_args()
    cfg = load_json(CONFIG_FILE, {})
    days = args.backfill or int(cfg.get("backfill_days", 7))
    lock = acquire_lock()
    if lock is None:
        log("已有分发进程在运行，退出")
        return 0
    try:
        os.makedirs(LOGS, exist_ok=True)
        with open(os.path.join(LOGS, "runner.pid"), "w") as f:
            f.write(str(os.getpid()))
        if args.dry_run:
            return one_round(cfg, days, dry_run=True)
        if args.loop:
            while True:
                try:
                    one_round(cfg, days)
                except Exception as e:
                    log("轮询异常: " + repr(e)[:200])
                time.sleep(60)
        return one_round(cfg, days)
    finally:
        try:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()
        except Exception:
            pass
        try:
            if open(os.path.join(LOGS, "runner.pid")).read().strip() == str(os.getpid()):
                os.unlink(os.path.join(LOGS, "runner.pid"))
        except (OSError, ValueError):
            pass


if __name__ == "__main__":
    raise SystemExit(main())
