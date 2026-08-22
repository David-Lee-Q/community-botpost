import datetime
import json
import os
import random
import sys
import uuid

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BOT_DIR)
PLAN_FILE = os.path.join(BOT_DIR, "plan.json")
ARTICLES_DIR = os.path.join(ROOT, "bot-articles")

sys.path.insert(0, BOT_DIR)
import one_shot as osx  # noqa: E402

LOOKAHEAD_DAYS = 3
DAILY_COUNT = 4
DAY_START, DAY_END = 8, 20  # 随机发布时间范围 08:00-20:00

TOPICS = {
    "人工智能": ["大模型推理成本下降对产业的影响", "多模态大模型在工业场景的落地", "AI Agent 重塑企业工作流",
                 "大模型进工厂的真实挑战", "模型微调与私有化部署实践"],
    "智能制造": ["柔性生产与个性化制造", "工厂数字化转型的常见误区", "工业质检AI化改造", "智能工厂的落地路径",
                 "中小制造企业低成本数字化"],
    "云计算": ["云成本优化实践", "混合云架构选型", "算力资源调度新范式", "云上高性能计算的成本账"],
    "云原生": ["容器与K8s运维实践", "平台工程落地", "服务网格与可观测性", "云原生安全实践"],
    "物联网": ["工业物联网连接数增长", "边缘设备接入与管理", "物联网数据质量治理", "5G+工业互联网落地"],
    "边缘计算": ["边缘AI推理部署", "云边协同架构", "边缘节点资源调度", "算力下沉到产线"],
    "大数据": ["数据治理体系建设", "数据仓库与湖仓一体", "数据要素资产化", "实时数据分析平台"],
    "区块链": ["供应链数据可信", "工业区块链落地场景", "数据确权与溯源", "联盟链实践"],
    "标识解析": ["工业标识体系", "一物一码全链路追溯", "标识解析二级节点", "设备数字身份"],
    "中间件": ["消息队列选型", "API网关实践", "微服务中间件治理", "高可用中间件架构"],
    "微服务": ["微服务拆分边界", "服务网格落地", "微服务可观测性", "分布式事务实践"],
    "安全": ["工业数据安全合规", "零信任架构落地", "AI对抗AI攻防", "供应链安全治理"],
    "编程与开发": ["AI辅助编程实践", "低代码开发平台", "开发者效率工具链", "代码质量工程化"],
    "网络": ["工业网络组网", "IPv6在工厂的部署", "网络切片与确定性网络", "园区网络运维"],
    "机器视觉": ["3D视觉缺陷检测", "视觉引导抓取", "工业相机选型", "视觉算法落地成本"],
    "工业操作系统": ["工业OS平台化", "工业软件国产化", "工业PaaS生态", "设备接入标准"],
    "数据要素": ["数据资产入表", "数据交易与流通", "数据要素市场化", "企业数据变现路径"],
}


def _now_str():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def random_times(date_str, n):
    """在 08:00-20:00 之间生成 n 个不重复的随机时分秒，升序返回。"""
    used = set()
    times = []
    while len(times) < n:
        h = random.randint(DAY_START, DAY_END - 1)
        m = random.randint(0, 59)
        s = random.randint(0, 59)
        key = (h, m, s)
        if key in used:
            continue
        used.add(key)
        times.append("%s %02d:%02d:%02d" % (date_str, h, m, s))
    return sorted(times)


def gen_one(direction, time_str, existing_titles):
    gen = osx.generate(direction)
    cover_path = None
    for _ in range(5):
        imgs = osx.pick_images(gen["category"])
        body = (gen["body"].replace("IMAGE1", imgs["body"][0])
                .replace("IMAGE2", imgs["body"][1]))
        cover_path = osx.build_cover_path(gen["category"])
        try:
            osx._download_cover(imgs["cover"], cover_path)
            break
        except Exception as e:
            print("封面下载失败(%s)，换图重试: %s" % (imgs["cover"], str(e)[:80]), flush=True)
            cover_path = None
    if cover_path is None:
        raise RuntimeError("封面下载多次失败，无法排期")

    os.makedirs(ARTICLES_DIR, exist_ok=True)
    article_file = os.path.join(
        ARTICLES_DIR,
        "scheduled-" + datetime.datetime.now().strftime("%Y%m%d%H%M%S") +
        "-" + str(random.randint(100, 999)) + ".md")
    with open(article_file, "w", encoding="utf-8") as f:
        f.write("# " + gen["title"] + "\n\n" + body)

    return {
        "time": time_str, "title": gen["title"], "category": gen["category"],
        "file": article_file, "summary": gen["summary"],
        "images": [imgs["body"][0], imgs["body"][1]],
        "cover": cover_path, "status": "pending",
        "taskId": uuid.uuid4().hex,
    }


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else LOOKAHEAD_DAYS
    plan = json.load(open(PLAN_FILE, encoding="utf-8"))
    schedule = plan["schedule"]
    migrated = False
    for it in schedule:
        if not it.get("taskId"):
            it["taskId"] = uuid.uuid4().hex
            migrated = True
    if migrated:
        with open(PLAN_FILE, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)
        print("gen_plan: 已为 %d 条计划补齐 taskId" % len(schedule))
    pending_times = {it.get("time") for it in schedule if it.get("status") == "pending"}
    existing_titles = {it.get("title") for it in schedule}

    now = datetime.datetime.now()
    added = 0
    count_by_date = {}
    for it in schedule:
        if it.get("status") in ("pending", "published") and it.get("time"):
            k = it["time"][:10]
            count_by_date[k] = count_by_date.get(k, 0) + 1
    for day in range(1, days + 1):
        d = now + datetime.timedelta(days=day)
        date_str = d.strftime("%Y-%m-%d")
        need = DAILY_COUNT - count_by_date.get(date_str, 0)
        if need <= 0:
            continue
        for time_str in random_times(date_str, need):
            category = random.choice(list(osx.CATEGORY_IDS.keys()))
            direction = random.choice(TOPICS[category])
            item = None
            for _ in range(3):
                try:
                    candidate = gen_one(direction, time_str, existing_titles)
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
            with open(PLAN_FILE, "w", encoding="utf-8") as f:
                json.dump(plan, f, ensure_ascii=False, indent=2)
            pending_times.add(time_str)
            existing_titles.add(item["title"])
            added += 1
            print("已排期 %s | %s | %s" % (time_str, item["category"], item["title"]), flush=True)

    if added:
        plan["schedule"] = schedule
        with open(PLAN_FILE, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)
        print("gen_plan: 新增 %d 篇计划，当前待发布 %d 篇" % (added, len(pending_times)), flush=True)
    else:
        print("gen_plan: 计划已充足，无新增", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
