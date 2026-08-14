import datetime
import json
import os
import subprocess
import sys
import time

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
PLAN_FILE = os.path.join(BOT_DIR, "plan.json")
HEARTBEAT = os.path.join(BOT_DIR, "HEARTBEAT.md")


def log(msg):
    line = f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)


def gen_heartbeat():
    with open(PLAN_FILE, encoding="utf-8") as f:
        plan = json.load(f)
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    published = [it for it in plan["schedule"] if it.get("status") == "published"]
    if not published:
        return
    lines = [f"# HEARTBEAT - 社区发文记录", "", f"## {today} 发布清单", "",
             "| 计划时间 | 标题 | 分类 | 文章ID | 详情URL | 状态 |",
             "|----------|------|------|--------|---------|------|"]
    for it in sorted(published, key=lambda x: x.get("time", "")):
        url = it.get("detail_url") or ""
        lines.append(
            f"| {it.get('time')} | {it['title']} | {it.get('category')} | "
            f"{it.get('article_id')} | {url} | 已发布 |"
        )
    lines += ["", "## 记录", "",
              "- 每日发布完成后，从「我的文章」页（/usercenter/1447/article/全部文章）按标题点击进入详情页，从地址栏获取真实详情 URL",
              "- 详情 URL 格式：https://openlab.cosmoplat.com/article-detils?id={文章ID}&articleType=0",
              "- 以 bot 实际发布返回的 article_id 为准记录 URL，避免列表页同名/重复文章干扰"]
    open(HEARTBEAT, "w", encoding="utf-8").write("\n".join(lines))
    log(f"HEARTBEAT 已更新，共 {len(published)} 篇")


def main():
    log("bot runner started")
    last_plan_day = None
    last_score_week = None
    last_fetch_hour = None
    last_daily = None
    last_weekly = None
    while True:
        now = datetime.datetime.now()
        with open(PLAN_FILE, encoding="utf-8") as f:
            plan = json.load(f)

        # 每 6 小时定时刷新台账数据（浏览/互动实时增长，保证趋势图数据最新）
        if now.hour % 6 == 0 and last_fetch_hour != now.date().isoformat() + str(now.hour):
            last_fetch_hour = now.date().isoformat() + str(now.hour)
            ledger = os.path.join(os.path.dirname(BOT_DIR), "ledger", "fetch_articles.py")
            if os.path.exists(ledger):
                log("定时刷新台账数据")
                rf = subprocess.run([sys.executable, ledger],
                                    capture_output=True, text=True, timeout=300)
                if rf.returncode != 0:
                    log("fetch_articles.py 失败: " + rf.stderr[-500:])
                else:
                    log(rf.stdout.strip())
            # 刷新后自动优化评分不合格的 bot 文章（评分<60 → AI优化+重新评分，直至达标或达上限）
            auto_opt = os.path.join(os.path.dirname(BOT_DIR), "ledger", "automate_optimize.py")
            if os.path.exists(auto_opt):
                log("检查并自动优化不合格文章")
                ao = subprocess.run([sys.executable, auto_opt],
                                    capture_output=True, text=True, timeout=1800)
                if ao.returncode != 0:
                    log("automate_optimize.py 失败: " + ao.stderr[-500:])
                else:
                    log(ao.stdout.strip())

        # 每日 18:00 推送飞书每日总结
        if now.hour == 18 and last_daily != now.date().isoformat():
            last_daily = now.date().isoformat()
            report = os.path.join(BOT_DIR, "report.py")
            if os.path.exists(report):
                log("推送每日总结")
                rd = subprocess.run([sys.executable, report, "daily"],
                                    capture_output=True, text=True, timeout=120)
                if rd.returncode != 0:
                    log("每日总结推送失败: " + rd.stderr[-500:])
                else:
                    log(rd.stdout.strip())

        # 每周一 10:00 推送飞书每周总结
        if now.weekday() == 0 and now.hour == 10 and last_weekly != now.date().isoformat():
            last_weekly = now.date().isoformat()
            report = os.path.join(BOT_DIR, "report.py")
            if os.path.exists(report):
                log("推送每周总结")
                rw = subprocess.run([sys.executable, report, "weekly"],
                                    capture_output=True, text=True, timeout=120)
                if rw.returncode != 0:
                    log("每周总结推送失败: " + rw.stderr[-500:])
                else:
                    log(rw.stdout.strip())

        if now.hour == 6 and last_plan_day != now.date().isoformat():
            last_plan_day = now.date().isoformat()
            log("6点已到，生成新一批计划")
            gen_plan = os.path.join(BOT_DIR, "gen_plan.py")
            if os.path.exists(gen_plan):
                gp = subprocess.run([sys.executable, gen_plan],
                                    capture_output=True, text=True, timeout=3600)
                if gp.returncode != 0:
                    log("gen_plan.py 失败: " + gp.stderr[-500:])
                else:
                    log(gp.stdout.strip())
            # 每周一 6 点更新综合评分（评分周期为上一完整周）
            if now.weekday() == 0:
                score_week = now.date().isoformat()
                if last_score_week != score_week:
                    last_score_week = score_week
                    review_engine = os.path.join(os.path.dirname(BOT_DIR), "ledger", "review_engine.py")
                    if os.path.exists(review_engine):
                        log("每周一更新综合评分")
                        r4 = subprocess.run([sys.executable, review_engine],
                                            capture_output=True, text=True, timeout=120)
                        if r4.returncode != 0:
                            log("review_engine.py 失败: " + r4.stderr[-500:])
                        else:
                            log(r4.stdout.strip())

        due = [it for it in plan["schedule"] if it.get("status") == "pending"
               and it.get("time") and it["time"] <= now.strftime("%Y-%m-%d %H:%M:%S")]
        if due:
            log(f"{len(due)} 篇文章到点，开始发布")
            r = subprocess.run([sys.executable, os.path.join(BOT_DIR, "publish.py")],
                               capture_output=True, text=True, timeout=300)
            print(r.stdout, flush=True)
            if r.returncode != 0:
                log("publish.py 失败: " + r.stderr[-500:])
            # 发布完成后抓取详情 URL
            log("抓取文章详情 URL")
            r2 = subprocess.run([sys.executable, os.path.join(BOT_DIR, "fetch_detail_urls.py")],
                                capture_output=True, text=True, timeout=300)
            print(r2.stdout, flush=True)
            if r2.returncode != 0:
                log("fetch_detail_urls.py 失败: " + r2.stderr[-500:])
            # 更新 HEARTBEAT 当日清单
            gen_heartbeat()
            # 刷新台账数据
            ledger = os.path.join(os.path.dirname(BOT_DIR), "ledger", "fetch_articles.py")
            if os.path.exists(ledger):
                log("刷新台账数据")
                r3 = subprocess.run([sys.executable, ledger],
                                    capture_output=True, text=True, timeout=300)
                if r3.returncode != 0:
                    log("fetch_articles.py 失败: " + r3.stderr[-500:])
                else:
                    log(r3.stdout.strip())
        time.sleep(30)


if __name__ == "__main__":
    main()
