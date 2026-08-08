import json
import os
import time
import requests

BASE = "https://openlab.cosmoplat.com/api"
MEMBER_ID = 1447
BOT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BOT_DIR, "data")
TOKEN_FILE = os.path.join(BOT_DIR, "..", "bot", "token.txt")


def get_token():
    return open(TOKEN_FILE).read().strip()


def fetch_all(token):
    h = {"s-user-token": token, "Content-Type": "application/json"}
    cate_map = {}
    r = requests.get(f"{BASE}/cate/list", headers=h, timeout=20)
    for c in r.json().get("data", []):
        cate_map[c["id"]] = c["name"]

    articles = []
    page, size = 1, 100
    while True:
        r = requests.post(
            f"{BASE}/member/article/{MEMBER_ID}?pageNum={page}&pageSize={size}",
            headers=h, json={}, timeout=30,
        )
        d = r.json()
        data = d.get("data", {})
        lst = data.get("list", [])
        if not lst:
            break
        for it in lst:
            articles.append({
                "id": it.get("id"),
                "title": it.get("title", "").strip(),
                "cateId": it.get("cateId"),
                "cateName": cate_map.get(it.get("cateId"), "未分类"),
                "createTime": it.get("createTime"),
                "status": it.get("status"),
                "viewCount": it.get("viewCount", 0),
                "commentCount": it.get("commentCount", 0),
                "favor": it.get("favor", 0),
                "collect": it.get("collect", 0),
                "shareCount": it.get("shareCount", 0),
            })
        total = data.get("total", 0)
        if page * size >= total:
            break
        page += 1
        time.sleep(0.3)
    return articles, cate_map


def main():
    token = get_token()
    articles, cate_map = fetch_all(token)
    payload = {
        "updatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "memberId": MEMBER_ID,
        "total": len(articles),
        "categories": {str(k): v for k, v in cate_map.items()},
        "articles": articles,
    }
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(os.path.join(DATA_DIR, "articles.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    print(f"已拉取 {len(articles)} 篇文章 -> {DATA_DIR}/articles.json")


if __name__ == "__main__":
    main()
