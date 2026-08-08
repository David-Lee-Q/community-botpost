import datetime
import json
import os

LEDGER = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(LEDGER, "data")
REVIEWS_FILE = os.path.join(DATA, "reviews.json")
ARTICLES_FILE = os.path.join(DATA, "articles.json")
OUT_FILE = os.path.join(DATA, "bot_score.json")
HISTORY_FILE = os.path.join(DATA, "score_history.json")

ROOT = os.path.dirname(LEDGER)
MEMORY_FILE = os.path.join(ROOT, ".cosmocode", "MEMORY.md")
RECORD_FILE = os.path.join(ROOT, ".cosmocode", "docs", "质量改进记录.md")
TASKS_FILE = os.path.join(ROOT, ".cosmocode", "quality_tasks.md")

DIM_ORDER = ["内容原创性", "技术准确性", "完整性结构", "可读表达", "规范合规", "传播表现"]

SUGGESTIONS = {
    "内容原创性": "增加一手实践案例与项目数据，减少行业共识性论述，突出增量观点",
    "技术准确性": "为关键数据与结论补充来源或时间标注，交叉核对术语与版本信息",
    "完整性结构": "强化「引言-分论点-结论」框架，每个分论点配数据、案例或引用其一",
    "可读表达": "每段聚焦单一论点并控制段落长度，增强小标题引导与图文呼应",
    "规范合规": "发布前逐项核对平台规范红线（封面尺寸、标题格式、配图版权）",
    "传播表现": "打磨标题信息量与钩子，摘要直击痛点，首段前三句抓住读者",
}


def week_range(today=None):
    today = today or datetime.date.today()
    if today.weekday() == 0:
        end = today - datetime.timedelta(days=1)
        start = end - datetime.timedelta(days=6)
    else:
        start = today - datetime.timedelta(days=today.weekday())
        end = today
    return start, end


def compute_score(reviews, arts, start, end):
    in_week = []
    for a in arts:
        if a.get("status") != 1:
            continue
        ct = (a.get("createTime") or "")[:10]
        if not ct:
            continue
        try:
            d = datetime.date.fromisoformat(ct)
        except ValueError:
            continue
        if start <= d <= end and str(a.get("id")) in reviews:
            in_week.append(a)
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not in_week:
        return {
            "week": f"{start}~{end}", "score": None, "articleCount": 0,
            "avgDimensions": {}, "weakest": [], "suggestions": ["本周无已评价的 bot 文章，暂无综合评分"],
            "updatedAt": now_str,
        }
    scores = [reviews[str(a["id"])]["score"] for a in in_week]
    score = round(sum(scores) / len(scores))
    dim_sums = {d: 0 for d in DIM_ORDER}
    for a in in_week:
        bd = reviews[str(a["id"])].get("breakdown", {})
        for d in DIM_ORDER:
            dim_sums[d] += bd.get(d, 0)
    avg_dims = {d: round(dim_sums[d] / len(in_week), 1) for d in DIM_ORDER}
    weakest = sorted(DIM_ORDER, key=lambda d: avg_dims[d])[:2]
    suggestions = [SUGGESTIONS[d] for d in weakest]
    if min(avg_dims.values()) >= 15:
        suggestions.append("整体质量稳定，建议尝试冲击更细分领域的深度选题")
    return {
        "week": f"{start}~{end}", "score": score, "articleCount": len(in_week),
        "avgDimensions": avg_dims, "weakest": weakest, "suggestions": suggestions,
        "updatedAt": now_str,
    }


def update_history(payload):
    hist = {"history": []}
    if os.path.exists(HISTORY_FILE):
        try:
            hist = json.load(open(HISTORY_FILE, encoding="utf-8"))
        except Exception:
            hist = {"history": []}
    hist["history"] = [h for h in hist["history"] if h.get("week") != payload["week"]]
    hist["history"].append({
        "week": payload["week"], "score": payload.get("score"),
        "articleCount": payload.get("articleCount"), "weakest": payload.get("weakest"),
        "suggestions": payload.get("suggestions"), "updatedAt": payload.get("updatedAt"),
    })
    hist["history"].sort(key=lambda h: h.get("week", ""))
    json.dump(hist, open(HISTORY_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return hist


def update_tasks(hist):
    import re
    done_map = {}
    if os.path.exists(TASKS_FILE):
        try:
            text = open(TASKS_FILE, encoding="utf-8").read()
            for m in re.finditer(r"- \[x\] (.+?)（完成日期：([^）]+)）", text):
                done_map[m.group(1)] = m.group(2)
            for m in re.finditer(r"- \[x\] (.+?)（建议日期", text):
                done_map.setdefault(m.group(1), "")
        except Exception:
            pass
    all_sugg = {}
    for h in hist.get("history", []):
        for s in h.get("suggestions", []):
            if s not in all_sugg:
                all_sugg[s] = h.get("week", "")
    for s in done_map:
        all_sugg.setdefault(s, "")
    lines = ["# 发文质量改进任务清单", "",
             "- 由 review_engine.py 每周一自动更新：基于综合评分建议生成改进任务", "",
             "## 待办改进项", ""]
    for s, week in all_sugg.items():
        if s in done_map:
            d = f"（完成日期：{done_map[s]}）" if done_map[s] else ""
            lines.append(f"- [x] {s}{d}")
        else:
            lines.append(f"- [ ] {s}（建议日期：{week}）")
    lines += ["", "## 说明", "",
              "- 每完成一项改进，将方括号改为 [x] 并在括号内注明完成日期", "- 新文章创作前请先核对待办项，未完成项即当前质量短板"]
    open(TASKS_FILE, "w", encoding="utf-8").write("\n".join(lines) + "\n")


def update_record(hist):
    rec = ["# 质量改进记录", "",
           "由 review_engine.py 每周一自动更新，记录每周综合评分趋势与优化建议。", "",
           "## 评分趋势", "", "| 评分周期 | 综合分 | 文章数 | 最弱维度 | 更新日期 |",
           "|----------|--------|--------|----------|----------|"]
    for h in hist.get("history", []):
        rec.append(f"| {h.get('week')} | {h.get('score') or '-'} | {h.get('articleCount')} | "
                   f"{'、'.join(h.get('weakest') or []) or '-'} | {h.get('updatedAt')} |")
    rec += ["", "## 优化建议汇总", ""]
    for h in hist.get("history", []):
        rec.append(f"### {h.get('week')}（评分 {h.get('score') or '-'}）")
        for s in h.get("suggestions", []):
            rec.append(f"- {s}")
        rec.append("")
    open(RECORD_FILE, "w", encoding="utf-8").write("\n".join(rec) + "\n")


def update_memory(payload):
    if not os.path.exists(MEMORY_FILE):
        return
    text = open(MEMORY_FILE, encoding="utf-8").read()
    weak_text = "、".join(payload.get("weakest") or []) or "—"
    block = ("[发文质量持续改进]\n"
             f"- Date: {datetime.date.today().isoformat()}\n"
             f"- Context: review_engine.py 每周一基于综合评分自动沉淀优化建议\n"
             f"- Instructions:\n"
             f"  - 每周一更新综合评分后，自动同步优化建议到 .cosmocode/docs/质量改进记录.md 与 .cosmocode/quality_tasks.md\n"
             f"  - 最新周期 {payload.get('week')} 综合分 {payload.get('score') or '-'}，当前短板维度：{weak_text}\n")
    for s in payload.get("suggestions", []):
        block += f"  - 改进要点：{s}\n"
    block += ("  - 创作新文章前，先核对 quality_tasks.md 的未完成改进项并主动应用\n"
              "  - 周综合评分低于 85 时，下批文章发布前必须优先落实对应改进要点\n")
    if "[发文质量持续改进]" in text:
        import re
        text = re.sub(r"\[发文质量持续改进\].*?(?=\n\[|\Z)", block.rstrip() + "\n", text, flags=re.S)
    else:
        text = text.rstrip() + "\n\n" + block.rstrip() + "\n"
    open(MEMORY_FILE, "w", encoding="utf-8").write(text)


def main():
    reviews = json.load(open(REVIEWS_FILE, encoding="utf-8"))
    arts = json.load(open(ARTICLES_FILE, encoding="utf-8")).get("articles", [])
    start, end = week_range()
    payload = compute_score(reviews, arts, start, end)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    hist = update_history(payload)
    update_tasks(hist)
    update_record(hist)
    update_memory(payload)
    print(f"综合评分 {payload.get('score')} 分（{payload['week']}，{payload['articleCount']} 篇）")
    print(f"已更新: bot_score.json / score_history.json / quality_tasks.md / 质量改进记录.md / MEMORY.md")


if __name__ == "__main__":
    main()
