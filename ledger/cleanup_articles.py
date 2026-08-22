import json
import os
import time

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

FILES = ["articles.json", "reviews.json", "article_scores.json",
         "content_scores.json", "article_bodies.json"]


def load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)


def save(name, obj):
    with open(os.path.join(DATA, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def main():
    art = load("articles.json")
    arts = art.get("articles", [])

    total_before = len(arts)
    published = [a for a in arts if a.get("status") == 1]
    failed = total_before - len(published)

    keep = []
    seen = set()
    dup_dropped = 0
    for a in sorted(published, key=lambda x: x.get("createTime", "")):
        t = (a.get("title") or "").strip()
        if t in seen:
            dup_dropped += 1
            continue
        seen.add(t)
        keep.append(a)

    keep_ids = {str(a["id"]) for a in keep}
    dropped_ids = {str(a["id"]) for a in arts} - keep_ids

    art["articles"] = keep
    art["total"] = len(keep)
    art["cleanedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save("articles.json", art)

    print(f"articles.json: {total_before} -> {len(keep)} 篇"
          f"（清理失败/草稿 {failed} 篇，标题重复 {dup_dropped} 篇）")

    for name in ["reviews.json", "article_scores.json", "content_scores.json", "article_bodies.json"]:
        data = load(name)
        before = len(data)
        data = {k: v for k, v in data.items() if k not in dropped_ids}
        save(name, data)
        print(f"{name}: {before} -> {len(data)} 条（清理 {before - len(data)} 条）")

    removed = sorted(dropped_ids, key=int)
    print("移除文章 id 列表:", ",".join(removed))


if __name__ == "__main__":
    main()
