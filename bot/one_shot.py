import datetime
import json
import os
import re
import subprocess
import sys

import requests

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BOT_DIR)
LEDGER = os.path.join(ROOT, "ledger")
STATUS_FILE = os.path.join(LEDGER, "data", "oneshot_status.json")
ARTICLES_DIR = os.path.join(BOT_DIR, "articles_tmp")
COVERS_DIR = os.path.join(BOT_DIR, "covers")

sys.path.insert(0, BOT_DIR)
from publish import CATEGORY_IDS, get_token, publish_one  # noqa: E402
import sensitive  # noqa: E402

IMG_BASE = "https://images.unsplash.com/{}?w=800&q=80"
COVER_BASE = "https://images.unsplash.com/{}?w=800&h=400&fit=crop&q=80"

CATEGORY_IMAGES = {
    "人工智能": ["photo-1677442136019-21780ecad995", "photo-1620712943543-bcc4688e7485"],
    "智能制造": ["photo-1581091226825-a6a2a5aee158", "photo-1563770660941-20978e870e26"],
    "云原生": ["photo-1550751827-4bd374c3f58b", "photo-1486312338219-ce68d2c6f44d"],
    "工业操作系统": ["photo-1451187580459-43490279c0fa", "photo-1504384308090-c894fdcc538d"],
    "大数据": ["photo-1550751827-4bd374c3f58b", "photo-1504384308090-c894fdcc538d"],
    "物联网": ["photo-1550751827-4bd374c3f58b", "photo-1486312338219-ce68d2c6f44d"],
    "边缘计算": ["photo-1504384308090-c894fdcc538d", "photo-1486312338219-ce68d2c6f44d"],
    "云计算": ["photo-1550751827-4bd374c3f58b", "photo-1486312338219-ce68d2c6f44d"],
    "机器视觉": ["photo-1620712943543-bcc4688e7485", "photo-1581091226825-a6a2a5aee158"],
    "安全": ["photo-1550751827-4bd374c3f58b", "photo-1451187580459-43490279c0fa"],
}
DEFAULT_IMAGES = ["photo-1451187580459-43490279c0fa", "photo-1504384308090-c894fdcc538d"]


def _llm(messages, max_tokens=12000):
    base = os.environ.get("MCAI_LLM_BASE_URL", "").rstrip("/")
    key = os.environ.get("MCAI_LLM_API_KEY", "")
    model = os.environ.get("MCAI_LLM_MODEL", "")
    r = requests.post(
        base + "/chat/completions",
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        json={"model": model, "messages": messages, "temperature": 0.6, "max_tokens": max_tokens},
        timeout=180,
    )
    d = r.json()
    if r.status_code != 200 or not d.get("choices"):
        raise RuntimeError("LLM调用失败: " + str(d)[:300])
    return d["choices"][0]["message"]["content"]


SYSTEM_PROMPT = (
    "你是卡奥斯开源社区的资深科技文章作者，擅长写出高传播、高可读的深度技术文章。"
    "输出必须是合法 JSON 对象，不要输出任何其他文字。JSON 结构："
    '{"title":"标题(不超过24字,不含冒号)","category":"分类","summary":"约120字摘要",'
    '"body":"markdown正文"}。'
    "内容质量要求（严格执行）："
    "1. 标题信息量大且有钩子：用具体数字、强观点、反差对比或读者痛点，"
    "避免「赋能」「新引擎」「新范式」「加速落地」「驶入快车道」等空泛套话；"
    "2. 摘要直击读者痛点并点明文章价值：先抛出问题或结论，再给出阅读价值；"
    "3. 正文不少于1200字，4-6段，每段200-400字，段间用二级标题分隔："
    "   a. 首段前三句必须抓住读者：用具体数据、真实痛点、反差场景或争议观点切入，"
    "   严禁「在...浪潮下」「随着...的发展」「近年来...成为热点」「众所周知」等套话开场；"
    "   b. 每段聚焦单一论点：段首第一句给出观点句，后文用论据支撑，"
    "   段落中不罗列并列条目（避免「第一...第二...第三...」式堆砌）；"
    "   c. 二级标题精炼有力、能概括段落核心观点，可带数字或反差；"
    "   d. 正文第1段后插入图片标记 ![配图1](IMAGE1)，第3段后插入 ![配图2](IMAGE2)，"
    "   图片说明文字与所在段落主题相关（图文呼应）；"
    "   e. 结尾段给出落地建议或趋势判断，避免戛然而止；"
    "4. 术语准确、数据克制、逻辑层层递进，围绕用户主题展开；"
    "5. 标题与正文均不得重复用户输入的原文标题；"
    "6. category 只能从以下选一：智能制造、云计算、云原生、物联网、边缘计算、人工智能、大数据、"
    "区块链、标识解析、中间件、微服务、安全、编程与开发、网络、机器视觉、工业操作系统、数据要素。"
)


def _parse_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except ValueError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise


def _download_cover(url, path):
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
    r.raise_for_status()
    if len(r.content) > 1 * 1024 * 1024:
        raise RuntimeError("封面超过1MB")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(r.content)


def generate(prompt):
    for attempt in range(3):
        try:
            content = _llm([
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": "主题：" + prompt},
            ])
            data = _parse_json(content)
        except Exception as e:
            if attempt < 2:
                continue
            raise RuntimeError("内容生成失败，请更换关键词重试（" + str(e)[:80] + "）")
        title = str(data.get("title", "")).strip()
        category = str(data.get("category", "")).strip()
        summary = str(data.get("summary", "")).strip()
        body = str(data.get("body", "")).strip()
        if category not in CATEGORY_IDS:
            category = "人工智能"
        if ":" in title:
            title = title.replace(":", "：")
        imgs = CATEGORY_IMAGES.get(category) or DEFAULT_IMAGES
        body = body.replace("IMAGE1", IMG_BASE.format(imgs[0]))
        body = body.replace("IMAGE2", IMG_BASE.format(imgs[1]))
        plain = body.replace("![配图1](" + IMG_BASE.format(imgs[0]) + ")", "") \
                    .replace("![配图2](" + IMG_BASE.format(imgs[1]) + ")", "")
        segments = [s for s in body.split("\n\n") if s.strip() and not s.startswith("!")]
        paras = [s for s in segments if not s.startswith("#")]
        first_para = paras[0] if paras else ""
        weak_title = re.search(r"(赋能|新引擎|新范式|加速落地|驶入快车道|按下快进键|开启新篇章)", title)
        cliche_open = re.search(
            r"^(在[^，。]{0,18}浪潮|随着[^，。]{0,24}发展|近年来[^，。]{0,10}成为|众所周知|当前[^，。]{0,10}正)", first_para)
        ok = (len(title) >= 6 and not weak_title and not cliche_open
              and len(plain) >= 1200
              and all(180 <= len(s) <= 420 for s in paras)
              and 100 <= len(summary) <= 160 and "IMAGE" not in body)
        if ok:
            return {"title": title, "category": category, "summary": summary,
                    "body": body, "cat_name": CATEGORY_IDS.get(category, "人工智能")}
    raise RuntimeError("内容生成未通过规范校验，请更换关键词重试")


def build_cover_path(category):
    key = category.lower().replace(" ", "-")
    idx = 1
    while True:
        p = os.path.join(COVERS_DIR, key, "cover" + str(idx) + ".jpg")
        if not os.path.exists(p):
            return p
        idx += 1


def run(prompt):
    gen = generate(prompt)
    imgs = CATEGORY_IMAGES.get(gen["category"]) or DEFAULT_IMAGES
    cover_path = build_cover_path(gen["category"])
    _download_cover(COVER_BASE.format(imgs[0]), cover_path)

    os.makedirs(ARTICLES_DIR, exist_ok=True)
    article_file = os.path.join(ARTICLES_DIR,
                                "oneshot-" + datetime.datetime.now().strftime("%Y%m%d%H%M%S") + ".md")
    with open(article_file, "w", encoding="utf-8") as f:
        f.write("# " + gen["title"] + "\n\n" + gen["body"])

    token = get_token()
    item = {
        "title": gen["title"], "category": gen["category"],
        "file": article_file, "summary": gen["summary"], "cover": cover_path,
    }

    hits = sensitive.check(gen["title"]) + sensitive.check(gen["summary"]) + sensitive.check(gen["body"])
    if hits:
        hits = sorted(set(hits))
        sensitive.log_record({
            "source": "发一篇", "article_title": gen["title"], "hits": hits,
            "action": "LLM内容优化",
        })
        item["title"] = sensitive.optimize_text(gen["title"], sensitive.check(gen["title"]), "标题") or gen["title"]
        item["summary"] = sensitive.optimize_text(gen["summary"], sensitive.check(gen["summary"]), "摘要") or gen["summary"]
        with open(article_file, "w", encoding="utf-8") as f:
            f.write("# " + item["title"] + "\n\n" + sensitive.optimize_text(gen["body"], hits, "正文"))
        gen["title"] = item["title"]

    result = publish_one(token, item)
    return gen, result, cover_path


def _write_status(state, detail):
    data = {"state": state, "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    data.update(detail)
    os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _register_post(aid, title):
    posts_file = os.path.join(LEDGER, "data", "bot_posts.json")
    try:
        posts = json.load(open(posts_file, encoding="utf-8")) if os.path.exists(posts_file) else {}
    except (OSError, ValueError):
        posts = {}
    if str(aid) not in posts:
        posts[str(aid)] = {"title": title, "cateName": "", "createTime": "", "source": "发一篇"}
        with open(posts_file, "w", encoding="utf-8") as f:
            json.dump(posts, f, ensure_ascii=False, indent=2)


def main():
    prompt = sys.argv[1] if len(sys.argv) > 1 else ""
    if not prompt:
        print("用法: one_shot.py <标题或关键词>")
        return 1
    hits = sensitive.check(prompt)
    if hits:
        sensitive.log_record({
            "source": "发一篇输入", "article_title": prompt, "hits": hits,
            "action": "拒绝（输入含敏感词）",
        })
        print(json.dumps({"state": "failed", "error": "输入内容包含敏感词：" + "、".join(hits)}, ensure_ascii=False))
        return 1
    _write_status("processing", {"prompt": prompt})
    try:
        gen, result, cover_path = run(prompt)
        _write_status("done", {"title": gen["title"], "result": result, "cover": cover_path})
        print(json.dumps({"state": "done", "title": gen["title"], "result": result}, ensure_ascii=False))
        if result.get("status") == "published":
            _register_post(result["article_id"], gen["title"])
        subprocess.run([sys.executable, os.path.join(LEDGER, "fetch_articles.py")],
                       capture_output=True, timeout=300)
    except Exception as e:
        _write_status("failed", {"error": str(e)})
        print(json.dumps({"state": "failed", "error": str(e)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
