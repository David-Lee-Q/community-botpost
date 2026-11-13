"""CSDN BizAPI 签名客户端（逆向自 CSDN同步助手 v1.2.1 扩展）。

签名算法：
  headers = {x-ca-key: KEY, x-ca-nonce: uuid4}
  canonical_headers = 按名称排序的 "name:value" 以换行连接
  待签名串 = METHOD\n*/*\naccept\ncontent-type\n\n{canonical_headers}\n{path}
    GET : accept="", content-type=""
    POST: accept="", content-type="application/json"
  x-ca-signature = base64(HMAC-SHA256(secret, 待签名串))
请求需携带 .csdn.net 的登录 Cookie（storage_state.json）。
"""
import base64
import hashlib
import hmac
import json
import os
import urllib.parse
import urllib.request
import uuid

BASE = "https://bizapi.csdn.net"
CA_KEY = "203803574"
CA_SECRET = "9znpamsyl2c7cdrr9sas0le9vbc3r6ba"
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "storage_state.json")

# 网关签名时按固定 schema 顺序重建 query（实测，未知参数一律 401）
QUERY_ORDER = {
    "/blog/phoenix/console/v1/article/list": ["pageNum", "pageSize", "status"],
}


def _cookies():
    try:
        data = json.load(open(STATE_FILE, encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for c in data.get("cookies", []):
        d = c.get("domain", "")
        if "csdn.net" in d:
            out.append(c)
    return out


class BizAPI:
    def __init__(self, cookies=None):
        self.cookies = cookies if cookies is not None else _cookies()

    def _cookie_header(self):
        return "; ".join("%s=%s" % (c["name"], c["value"]) for c in self.cookies)

    def _sign(self, method, path, extra=None):
        headers = {"x-ca-key": CA_KEY, "x-ca-nonce": str(uuid.uuid4())}
        if extra:
            for k, v in extra.items():
                lk = k.lower()
                if lk.startswith("x-ca-") and lk not in ("x-ca-signature", "x-ca-signature-headers"):
                    headers[k] = v
        items = sorted(headers.items(), key=lambda kv: kv[0])
        canon = "\n".join("%s:%s" % (k, v) for k, v in items)
        signed_names = ",".join(k for k, _ in items)
        ctype = "application/json" if method == "POST" else ""
        sts = "\n".join([method, "*/*", "", ctype, "", canon, path])
        sig = base64.b64encode(
            hmac.new(CA_SECRET.encode(), sts.encode(), hashlib.sha256).digest()
        ).decode()
        out = {"accept": "*/*", **dict(items),
               "x-ca-signature": sig, "x-ca-signature-headers": signed_names,
               "cookie": self._cookie_header(),
               "origin": "https://mp.csdn.net",
               "referer": "https://mp.csdn.net/"}
        if method == "POST":
            out["content-type"] = "application/json"
        return out

    def canonical_path(self, path):
        """按网关 schema 顺序重建 query 用于签名。"""
        if "?" not in path:
            return path
        p, q = path.split("?", 1)
        order = QUERY_ORDER.get(p)
        if not order:
            return path
        pairs = urllib.parse.parse_qsl(q, keep_blank_values=True)
        kept = [(k, v) for k, v in pairs if k in order]
        if len(kept) != len(pairs):
            return path
        canon = "&".join("%s=%s" % (k, v) for k, v in
                         sorted(kept, key=lambda kv: order.index(kv[0])))
        return p + "?" + canon

    def request(self, method, path, body=None, timeout=30):
        url = BASE + path
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode()
        req = urllib.request.Request(url, data=data, method=method,
                                     headers=self._sign(method, self.canonical_path(path)))
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode())
            except Exception:
                return {"__http__": e.code}

    def get(self, path, timeout=30):
        return self.request("GET", path, None, timeout)

    def post(self, path, body, timeout=30):
        return self.request("POST", path, body, timeout)


def article_list(api, status="all_v3", page=1, size=20):
    return api.get("/blog/phoenix/console/v1/article/list?status=%s&pageSize=%d&pageNum=%d"
                   % (status, size, page))


def save_article(api, payload):
    return api.post("/blog-console-api/v3/mdeditor/saveArticle", payload)


if __name__ == "__main__":
    api = BizAPI()
    r = api.get("/blog/phoenix/console/v1/user/list-permission")
    print("list-permission code:", r.get("code"), str(r)[:160])
    r = article_list(api)
    lst = (r.get("data") or {}).get("list") or []
    print("article count:", (r.get("data") or {}).get("count", {}).get("all_v3"))
    for it in lst[:4]:
        print(" ", it.get("articleId"), it.get("title", "")[:28],
              "cover:", bool(it.get("coverImage")))
