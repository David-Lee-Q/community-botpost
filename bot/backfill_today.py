import datetime
import json
import random
import sys

import gen_plan as gp
import one_shot as osx


def main():
    now = datetime.datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    plan = json.load(open(gp.PLAN_FILE, encoding="utf-8"))
    schedule = plan["schedule"]
    existing_titles = {it.get("title") for it in schedule}

    start = (now + datetime.timedelta(minutes=45)).replace(second=0, microsecond=0)
    end = now.replace(hour=19, minute=30, second=0, microsecond=0)
    if start >= end:
        print("backfill_today: 今天剩余时段不足，放弃", flush=True)
        return 1
    step = (end - start).total_seconds() / 3
    slots = [
        (start + datetime.timedelta(seconds=int(step * i))).strftime("%Y-%m-%d %H:%M:%S")
        for i in range(4)
    ]

    added = 0
    for time_str in slots:
        category = random.choice(list(osx.CATEGORY_IDS.keys()))
        direction = random.choice(gp.TOPICS[category])
        item = None
        for _ in range(3):
            try:
                candidate = gp.gen_one(direction, time_str, existing_titles)
            except Exception as e:
                print("生成失败(%s)：%s" % (direction, str(e)[:120]), flush=True)
                candidate = None
            if candidate and candidate["title"] not in existing_titles:
                item = candidate
                break
        if item is None:
            print("跳过时段 %s：多次生成失败" % time_str, flush=True)
            continue
        schedule.append(item)
        plan["schedule"] = schedule
        gp._write_json(gp.PLAN_FILE, plan)
        existing_titles.add(item["title"])
        added += 1
        print("已排期 %s | %s | %s" % (time_str, item["category"], item["title"]), flush=True)
    print("backfill_today: 今日新增 %d 篇计划" % added, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
