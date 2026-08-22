import datetime
import json
import os
import random
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
USED_FILE = os.path.join(BOT_DIR, "used_images.json")

sys.path.insert(0, BOT_DIR)
from publish import CATEGORY_IDS, get_token, publish_one  # noqa: E402
import sensitive  # noqa: E402

IMG_BASE = "https://images.unsplash.com/{}?w=800&q=80"
COVER_BASE = "https://images.unsplash.com/{}?w=800&h=400&fit=crop&q=80"
PEXELS_BASE = "https://images.pexels.com/photos/{}/pexels-photo-{}.jpeg?auto=compress&cs=tinysrgb"


def _img_key(item):
    return "{}:{}".format(item["site"], item["ref"])


def _img_url(item, kind):
    """kind: body | cover"""
    site, ref = item["site"], item["ref"]
    if site == "unsplash":
        return IMG_BASE.format(ref) if kind == "body" else COVER_BASE.format(ref)
    if site == "pexels":
        if kind == "body":
            return PEXELS_BASE.format(ref, ref) + "&w=800"
        return PEXELS_BASE.format(ref, ref) + "&w=800&h=400&fit=crop"
    return ref

IMAGE_POOL = [
    {"site": "unsplash", "ref": "photo-1485827404703-89b55fcc595e", "theme": "ai"},
    {"site": "unsplash", "ref": "photo-1555255707-c07966088b7b", "theme": "ai"},
    {"site": "unsplash", "ref": "photo-1531746790731-6c087fecd65a", "theme": "ai"},
    {"site": "unsplash", "ref": "photo-1484557985045-edf25e08da73", "theme": "ai"},
    {"site": "unsplash", "ref": "photo-1580757468214-c73f7062a5cb", "theme": "ai"},
    {"site": "unsplash", "ref": "photo-1620712943543-bcc4688e7485", "theme": "ai"},
    {"site": "unsplash", "ref": "photo-1677442136019-21780ecad995", "theme": "ai"},
    {"site": "unsplash", "ref": "photo-1569012871812-f38ee64cd54c", "theme": "ai"},
    {"site": "unsplash", "ref": "photo-1639762681485-074b7f938ba0", "theme": "ai"},
    {"site": "unsplash", "ref": "photo-1639762681057-408e52192e55", "theme": "ai"},
    {"site": "unsplash", "ref": "photo-1620714223084-8fcacc6dfd8d", "theme": "ai"},
    {"site": "unsplash", "ref": "photo-1526374965328-7f61d4dc18c5", "theme": "code"},
    {"site": "unsplash", "ref": "photo-1498050108023-c5249f4df085", "theme": "code"},
    {"site": "unsplash", "ref": "photo-1461749280684-dccba630e2f6", "theme": "code"},
    {"site": "unsplash", "ref": "photo-1555066931-4365d14bab8c", "theme": "code"},
    {"site": "unsplash", "ref": "photo-1542831371-29b0f74f9713", "theme": "code"},
    {"site": "unsplash", "ref": "photo-1555949963-aa79dcee981c", "theme": "code"},
    {"site": "unsplash", "ref": "photo-1504639725590-34d0984388bd", "theme": "code"},
    {"site": "unsplash", "ref": "photo-1580927752452-89d86da3fa0a", "theme": "code"},
    {"site": "unsplash", "ref": "photo-1551650975-87deedd944c3", "theme": "code"},
    {"site": "unsplash", "ref": "photo-1526628953301-3e589a6a8b74", "theme": "dc"},
    {"site": "unsplash", "ref": "photo-1607252650355-f7fd0460ccdb", "theme": "dc"},
    {"site": "unsplash", "ref": "photo-1558494949-4314304c7b42", "theme": "dc"},
    {"site": "unsplash", "ref": "photo-1590959651373-a3db0f38a961", "theme": "dc"},
    {"site": "unsplash", "ref": "photo-1555617981-dac3880eac6e", "theme": "dc"},
    {"site": "unsplash", "ref": "photo-1518770660439-4636190af475", "theme": "dc"},
    {"site": "unsplash", "ref": "photo-1555664424-778a1e5e1b48", "theme": "dc"},
    {"site": "unsplash", "ref": "photo-1593642632823-8f785ba67e45", "theme": "dc"},
    {"site": "unsplash", "ref": "photo-1544197150-b99a580bb7a8", "theme": "dc"},
    {"site": "unsplash", "ref": "photo-1467232004584-a241de8bcf5d", "theme": "dc"},
    {"site": "unsplash", "ref": "photo-1504868584819-f8e8b4b6d7e3", "theme": "dc"},
    {"site": "unsplash", "ref": "photo-1593642532744-d377ab507dc8", "theme": "dc"},
    {"site": "unsplash", "ref": "photo-1565043666747-69f6646db940", "theme": "ind"},
    {"site": "unsplash", "ref": "photo-1581092160562-40aa08e78837", "theme": "ind"},
    {"site": "unsplash", "ref": "photo-1581091226825-a6a2a5aee158", "theme": "ind"},
    {"site": "unsplash", "ref": "photo-1563770660941-20978e870e26", "theme": "ind"},
    {"site": "unsplash", "ref": "photo-1531297484001-80022131f5a1", "theme": "ind"},
    {"site": "unsplash", "ref": "photo-1581094794329-c8112a89af12", "theme": "ind"},
    {"site": "unsplash", "ref": "photo-1565793298595-6a879b1d9492", "theme": "ind"},
    {"site": "unsplash", "ref": "photo-1516937941344-00b4e0337589", "theme": "ind"},
    {"site": "unsplash", "ref": "photo-1565193566173-7a0ee3dbe261", "theme": "ind"},
    {"site": "unsplash", "ref": "photo-1586528116311-ad8dd3c8310d", "theme": "ind"},
    {"site": "unsplash", "ref": "photo-1537462715879-360eeb61a0ad", "theme": "ind"},
    {"site": "unsplash", "ref": "photo-1587049352846-4a222e784d38", "theme": "ind"},
    {"site": "unsplash", "ref": "photo-1532073150508-0c1df022bdd1", "theme": "ind"},
    {"site": "unsplash", "ref": "photo-1524749292158-7540c2494485", "theme": "ind"},
    {"site": "unsplash", "ref": "photo-1587620962725-abab7fe55159", "theme": "ind"},
    {"site": "unsplash", "ref": "photo-1551288049-bebda4e38f71", "theme": "data"},
    {"site": "unsplash", "ref": "photo-1460925895917-afdab827c52f", "theme": "data"},
    {"site": "unsplash", "ref": "photo-1564865878688-9a244444042a", "theme": "data"},
    {"site": "unsplash", "ref": "photo-1558591710-4b4a1ae0f04d", "theme": "data"},
    {"site": "unsplash", "ref": "photo-1553877522-43269d4ea984", "theme": "data"},
    {"site": "unsplash", "ref": "photo-1559526324-4b87b5e36e44", "theme": "data"},
    {"site": "unsplash", "ref": "photo-1560472354-b33ff0c44a43", "theme": "data"},
    {"site": "unsplash", "ref": "photo-1450101499163-c8848c66ca85", "theme": "data"},
    {"site": "unsplash", "ref": "photo-1551434678-e076c223a692", "theme": "data"},
    {"site": "unsplash", "ref": "photo-1451187580459-43490279c0fa", "theme": "abs"},
    {"site": "unsplash", "ref": "photo-1504384308090-c894fdcc538d", "theme": "abs"},
    {"site": "unsplash", "ref": "photo-1486312338219-ce68d2c6f44d", "theme": "abs"},
    {"site": "unsplash", "ref": "photo-1550751827-4bd374c3f58b", "theme": "abs"},
    {"site": "unsplash", "ref": "photo-1527430253228-e93688616381", "theme": "abs"},
    {"site": "unsplash", "ref": "photo-1531403009284-440f080d1e12", "theme": "abs"},
    {"site": "unsplash", "ref": "photo-1526666923127-b2970f64b422", "theme": "abs"},
    {"site": "unsplash", "ref": "photo-1554224155-6726b3ff858f", "theme": "abs"},
    {"site": "unsplash", "ref": "photo-1573855619003-97b4799dcd8b", "theme": "abs"},
    {"site": "unsplash", "ref": "photo-1568819317551-31051b8b7f8e", "theme": "abs"},
    {"site": "unsplash", "ref": "photo-1622979135225-d2ba269cf1ac", "theme": "abs"},
    {"site": "unsplash", "ref": "photo-1523287562758-66c7fc58967f", "theme": "abs"},
    {"site": "unsplash", "ref": "photo-1537432376769-00f5c2f4c8d2", "theme": "abs"},
    {"site": "unsplash", "ref": "photo-1535223289827-42f1e9919769", "theme": "abs"},
    {"site": "unsplash", "ref": "photo-1535378620166-273708d44e4c", "theme": "abs"},
    {"site": "unsplash", "ref": "photo-1550745165-9bc0b252726f", "theme": "abs"},
    {"site": "unsplash", "ref": "photo-1519452575417-564c1401ecc0", "theme": "abs"},
]


def _load_used():
    try:
        return json.load(open(USED_FILE, encoding="utf-8"))
    except (OSError, ValueError):
        return {"used_ids": [], "articles": {}}


def _save_used(used):
    import tempfile
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(USED_FILE), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(used, f, ensure_ascii=False, indent=2)
    os.replace(tmp, USED_FILE)


THEMES = {
    "人工智能": ["ai", "data", "abs"],
    "智能制造": ["ind", "ai", "abs"],
    "机器视觉": ["ai", "ind", "abs"],
    "云计算": ["dc", "abs", "data"],
    "云原生": ["dc", "abs", "code"],
    "物联网": ["abs", "dc", "ind"],
    "边缘计算": ["dc", "abs", "code"],
    "大数据": ["data", "abs", "dc"],
    "安全": ["abs", "dc", "code"],
    "工业操作系统": ["ind", "abs", "dc"],
    "数据要素": ["data", "abs", "dc"],
    "编程与开发": ["code", "abs", "dc"],
    "中间件": ["code", "abs", "dc"],
    "微服务": ["code", "abs", "dc"],
    "区块链": ["abs", "dc", "code"],
    "标识解析": ["abs", "dc"],
    "网络": ["abs", "dc"],
}
_DEFAULT_THEMES = ["abs", "dc", "ai"]


def pick_images(category="人工智能"):
    """按文章分类从对应主题组随机取 3 张（正文2+封面1），排除已用图并记录。
    主题组不足 3 张时扩展相邻组；全池耗尽时重置（保留最近 9 张避免立即重复）。
    返回 {"body": [url1, url2], "cover": url, "items": [item1, item2, item3]}。"""
    used = _load_used()
    used_ids = set(used.get("used_ids", []))
    themes = THEMES.get(category) or _DEFAULT_THEMES
    cand = []
    for t in themes:
        cand += [i for i in IMAGE_POOL if i["theme"] == t and _img_key(i) not in used_ids]
    if len(cand) < 3:
        cand += [i for i in IMAGE_POOL if _img_key(i) not in used_ids and i not in cand]
    if len(cand) < 3:
        recent = used.get("recent", [])
        used_ids = set(recent)
        used["used_ids"] = list(recent)
        cand = [i for i in IMAGE_POOL if _img_key(i) not in used_ids]
    random.shuffle(cand)
    picks = cand[:3]
    keys = [_img_key(i) for i in picks]
    new_ids = sorted(set(used_ids) | set(keys))
    used["used_ids"] = new_ids
    used["recent"] = new_ids[-9:]
    _save_used(used)
    return {"body": [_img_url(picks[0], "body"), _img_url(picks[1], "body")],
            "cover": _img_url(picks[2], "cover"),
            "items": picks}


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
    "3. 正文不少于1200字，4-6段，每段200-400字，段间用二级标题分隔，"
    "整体遵循「引言-分论点-结论」框架："
    "   a. 引言（首段）：前三句必须抓住读者——用具体数据、真实痛点、反差场景或争议观点切入，"
    "   段末抛出全文要论证的核心论点或悬念；严禁「在...浪潮下」「随着...的发展」"
    "   「近年来...成为热点」「众所周知」等套话开场；"
    "   b. 每个分论点（正文段）：段首第一句给出观点句，且至少配一个具体数据、案例或引用支撑"
    "   （三者选一即可，禁止只有观点没有论据），段落中不罗列并列条目"
    "   （避免「第一...第二...第三...」式堆砌）；"
    "   c. 二级标题精炼有力、能概括段落核心观点，可带数字或反差；"
    "   d. 正文第1段后插入图片标记 ![配图1](IMAGE1)，第3段后插入 ![配图2](IMAGE2)，"
    "   图片说明文字与所在段落主题相关（图文呼应）；"
    "   e. 结论（结尾段）：呼应引言论点并给出落地建议或趋势判断，避免戛然而止；"
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
        plain = body.replace("![配图1](IMAGE1)", "").replace("![配图2](IMAGE2)", "")
        segments = [s for s in body.split("\n\n") if s.strip() and not s.startswith("!")]
        paras = [s for s in segments if not s.startswith("#")]
        first_para = paras[0] if paras else ""
        weak_title = re.search(r"(赋能|新引擎|新范式|加速落地|驶入快车道|按下快进键|开启新篇章)", title)
        cliche_open = re.search(
            r"^(在[^，。]{0,18}浪潮|随着[^，。]{0,24}发展|近年来[^，。]{0,10}成为|众所周知|当前[^，。]{0,10}正)", first_para)
        ok = (len(title) >= 6 and not weak_title and not cliche_open
              and len(plain) >= 1200
              and all(180 <= len(s) <= 420 for s in paras)
              and 100 <= len(summary) <= 160 and "IMAGE" not in body.replace("IMAGE1", "").replace("IMAGE2", ""))
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
    imgs = pick_images(gen["category"])
    gen["body"] = (gen["body"].replace("IMAGE1", imgs["body"][0])
                   .replace("IMAGE2", imgs["body"][1]))
    cover_path = build_cover_path(gen["category"])
    _download_cover(imgs["cover"], cover_path)

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
        opt_body = sensitive.optimize_text(gen["body"], hits, "正文") or gen["body"]
        with open(article_file, "w", encoding="utf-8") as f:
            f.write("# " + item["title"] + "\n\n" + opt_body)
        gen["title"] = item["title"]

    result = publish_one(token, item)
    if result.get("status") == "published" and result.get("article_id"):
        used = _load_used()
        used["articles"][str(result["article_id"])] = [_img_key(i) for i in imgs["items"]]
        _save_used(used)
    return gen, result, cover_path


def _write_status(state, detail):
    import tempfile
    data = {"state": state, "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    data.update(detail)
    os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(STATUS_FILE), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATUS_FILE)


def _register_post(aid, title):
    import tempfile
    posts_file = os.path.join(LEDGER, "data", "bot_posts.json")
    try:
        posts = json.load(open(posts_file, encoding="utf-8")) if os.path.exists(posts_file) else {}
    except (OSError, ValueError):
        posts = {}
    if str(aid) not in posts:
        posts[str(aid)] = {"title": title, "cateName": "", "createTime": "", "source": "发一篇"}
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(posts_file), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(posts, f, ensure_ascii=False, indent=2)
        os.replace(tmp, posts_file)


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
