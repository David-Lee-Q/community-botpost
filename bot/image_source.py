"""动态配图源：优先用 Pexels 按文章关键词搜图，失败回退静态主题图池。

- API key 读取顺序：环境变量 PEXELS_API_KEY → bot/pexels_key.txt（不入 git）
- 返回结构与 one_shot.pick_images 一致：{"body":[url,url], "cover":url, "items":[item,item,item]}
- item = {"site": "pexels", "ref": "<photo_id>"}，图片 URL 由调用方按 _img_url 拼接，
  远程 URL 在发布时统一转存平台 OSS，不会出现在正文里（绕过平台 WAF）
"""
import os
import random

import requests

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
KEY_FILE = os.path.join(BOT_DIR, "pexels_key.txt")
PEXELS_API = "https://api.pexels.com/v1/search"
PEXELS_BASE = "https://images.pexels.com/photos/{}/pexels-photo-{}.jpeg?auto=compress&cs=tinysrgb"
_UA = {"User-Agent": "Mozilla/5.0"}

QUERIES = {
    "人工智能": ["artificial intelligence", "neural network", "machine learning", "ai technology"],
    "智能制造": ["smart factory", "industrial automation", "manufacturing robot", "robotic arm"],
    "机器视觉": ["computer vision", "machine vision", "quality inspection", "industrial camera"],
    "云计算": ["cloud computing", "data center servers", "cloud technology", "server room"],
    "云原生": ["server container", "devops programming", "network infrastructure", "kubernetes"],
    "物联网": ["iot sensors", "smart devices network", "connected technology", "industrial sensor"],
    "边缘计算": ["edge computing", "network server", "technology infrastructure", "data center"],
    "大数据": ["big data analytics", "data visualization", "data science", "analytics dashboard"],
    "安全": ["cyber security", "network security", "data protection", "encryption technology"],
    "工业操作系统": ["industrial control", "factory machinery", "industrial equipment", "automation panel"],
    "数据要素": ["data analytics", "digital data", "data visualization", "business analytics"],
    "编程与开发": ["programming code", "software developer", "coding screen", "developer workspace"],
    "中间件": ["server infrastructure", "network servers", "software architecture", "data center"],
    "微服务": ["software architecture", "network connection", "cloud infrastructure", "source code"],
    "区块链": ["blockchain technology", "digital ledger", "cryptography", "digital network"],
    "标识解析": ["qr code scanning", "digital identity", "barcode scanner", "tracking technology"],
    "网络": ["network cable", "internet connection", "server network", "fiber optic"],
}
_DEFAULT_QUERIES = ["technology", "digital innovation", "futuristic technology"]


def get_key():
    k = os.environ.get("PEXELS_API_KEY", "").strip()
    if k:
        return k
    try:
        with open(KEY_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _img_url(photo_id, kind):
    base = PEXELS_BASE.format(photo_id, photo_id)
    return base + "&w=800" if kind == "body" else base + "&w=800&h=400&fit=crop"


def _search(query, page=1, per_page=40):
    r = requests.get(
        PEXELS_API,
        headers={"Authorization": get_key()},
        params={"query": query, "per_page": per_page, "page": page, "orientation": "landscape"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json().get("photos", [])


def _valid(url, max_bytes):
    try:
        r = requests.get(url, headers=_UA, stream=True, timeout=30)
        if r.status_code != 200:
            return False
        ct = r.headers.get("Content-Type", "")
        if "image" not in ct:
            return False
        cl = int(r.headers.get("Content-Length") or 0)
        if cl and cl > max_bytes:
            return False
        chunk = next(r.iter_content(1024), b"")
        return len(chunk) > 0
    except Exception:
        return False


def pick(category, hint="", used_ids=None):
    """返回 3 个可用图 item（含校验），无 key 或全部失败时返回 []。"""
    if not get_key():
        return []
    used_ids = used_ids or set()
    query = (hint or "").strip() or random.choice(QUERIES.get(category) or _DEFAULT_QUERIES)
    cands, seen = [], set()
    for page in random.sample([1, 2, 3, 4], 2) + [1, 2, 3, 4]:
        try:
            photos = _search(query, page=page)
        except Exception:
            break
        for p in photos:
            pid = str(p.get("id"))
            key = "pexels:" + pid
            if key in used_ids or key in seen:
                continue
            seen.add(key)
            cands.append((key, pid))
        if len(cands) >= 10:
            break
    random.shuffle(cands)
    picked = []
    for key, pid in cands:
        if _valid(_img_url(pid, "body"), 900 * 1024) and _valid(_img_url(pid, "cover"), 1024 * 1024):
            picked.append((key, pid))
        if len(picked) == 3:
            break
    return [{"site": "pexels", "ref": pid, "key": key} for key, pid in picked]
