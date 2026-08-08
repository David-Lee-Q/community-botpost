import datetime
import json
import os
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


def _llm(messages, max_tokens=8000):
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
    "你是卡奥斯开源社区的科技文章作者。输出必须是合法 JSON 对象，不要输出任何其他文字。JSON 结构："
    '{"title":"标题(不超过24字,不含冒号)","category":"分类","summary":"约120字摘要",'
    '"body":"markdown正文"}。要求：'
    "1. 正文不少于1000字，每段不少于200字，共4-6段，段间用二级标题分隔；"
    "2. 正文第1段后插入图片标记 ![配图1](IMAGE1)，第3段后插入 ![配图2](IMAGE2)；"
    "3. 正文内容围绕用户给出的主题，观点明确、逻辑递进、术语准确；"
    "4. 标题与正文均不得重复用户输入的原文标题；"
    "5. category 只能从以下选一：智能制造、云计算、云原生、物联网、边缘计算、人工智能、大数据、"
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
    for attempt in range(2):
        content = _llm([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "主题：" + prompt},
        ])
        data = _parse_json(content)
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
        ok = (len(title) >= 6 and len(plain) >= 1000
              and all(len(s) >= 150 for s in segments if not s.startswith("#"))
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
    result = publish_one(token, item)
    return gen, result, cover_path


def _write_status(state, detail):
    data = {"state": state, "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    data.update(detail)
    os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main():
    prompt = sys.argv[1] if len(sys.argv) > 1 else ""
    if not prompt:
        print("用法: one_shot.py <标题或关键词>")
        return 1
    _write_status("processing", {"prompt": prompt})
    try:
        gen, result, cover_path = run(prompt)
        _write_status("done", {"title": gen["title"], "result": result, "cover": cover_path})
        print(json.dumps({"state": "done", "title": gen["title"], "result": result}, ensure_ascii=False))
        subprocess.run([sys.executable, os.path.join(LEDGER, "fetch_articles.py")],
                       capture_output=True, timeout=300)
    except Exception as e:
        _write_status("failed", {"error": str(e)})
        print(json.dumps({"state": "failed", "error": str(e)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
