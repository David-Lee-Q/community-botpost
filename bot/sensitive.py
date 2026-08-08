import datetime
import json
import os
import re

import requests

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
WORD_DIR = os.path.join(BOT_DIR, "sensitive_words")
LOG_FILE = os.path.join(BOT_DIR, "sensitive_log.json")

_pool = []
_patterns = []
_chunk_size = 800

CORE_WORDS = [
    "赌博", "博彩", "赌球", "赌场", "赌资", "开设赌场", "网络赌场", "地下赌场",
    "涉赌", "涉黄", "涉毒", "黄赌毒", "网络赌博",
    "毒品", "冰毒", "海洛因", "大麻", "摇头丸", "吸食毒品", "制毒", "贩毒", "毒品交易",
    "色情", "淫秽", "嫖娼", "卖淫", "裸聊", "色情网站", "淫秽视频", "黄色网站",
    "色情直播", "性感直播", "大尺度", "成人影片", "艳照", "福利资源", "资源分享",
    "性感美女", "美女直播", "深夜福利", "裸体", "露骨", "情色", "黄色录像",
    "诈骗", "电信诈骗", "刷单", "跑分", "洗钱", "套路贷", "裸贷", "杀猪盘",
    "枪支", "弹药", "制枪", "买枪", "手雷", "炸药", "爆破物", "仿真枪",
    "杀人", "绑架", "恐怖主义", "极端主义", "自杀式袭击",
    "法轮功", "台独", "藏独", "疆独", "东突", "六四",
    "代开发票", "办证刻章", "代办证件", "买卖银行卡", "套现",
]

# 高频中性技术词，用于排除词库误报
WHITELIST = {
    "网络", "今天", "支持", "安全", "正常", "平台", "系统", "服务", "数据",
    "技术", "管理", "建设", "发展", "用户", "企业", "信息", "项目", "中国",
    "软件", "硬件", "产品", "设备", "应用", "方案", "能力", "场景", "领域",
    "行业", "模式", "体系", "架构", "模型", "算法", "智能", "工业", "制造",
    "数字", "云", "网络化", "平台化", "互联网", "自动化",
}


def load_words():
    global _pool, _patterns
    words = set(CORE_WORDS)
    for fn in os.listdir(WORD_DIR):
        if fn == "stopword.dic":
            continue
        if not fn.endswith(".txt") and not fn.endswith(".dic"):
            continue
        path = os.path.join(WORD_DIR, fn)
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                w = line.strip()
                if not w or len(w) < 2 or len(w) > 32:
                    continue
                if all(c in "，。！？、；：""''（）【】《》～~·,.!?;:\"'()[]{}<>/-_#@*&%$^+=|\\ \t" for c in w):
                    continue
                if w in WHITELIST:
                    continue
                words.add(w)
    _pool = sorted(words)
    _patterns = [
        re.compile("|".join(re.escape(w) for w in _pool[i:i + _chunk_size]))
        for i in range(0, len(_pool), _chunk_size)
    ]
    return _pool


def ensure_loaded():
    if not _patterns:
        load_words()


def check(text):
    ensure_loaded()
    if not text:
        return []
    hits = set()
    for p in _patterns:
        for m in p.findall(text):
            hits.add(m)
    return sorted(hits)


def _llm(messages, max_tokens=2000):
    base = os.environ.get("MCAI_LLM_BASE_URL", "").rstrip("/")
    key = os.environ.get("MCAI_LLM_API_KEY", "")
    model = os.environ.get("MCAI_LLM_MODEL", "")
    r = requests.post(
        base + "/chat/completions",
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        json={"model": model, "messages": messages, "temperature": 0.5, "max_tokens": max_tokens},
        timeout=180,
    )
    d = r.json()
    if r.status_code != 200 or not d.get("choices"):
        raise RuntimeError("LLM调用失败: " + str(d)[:300])
    return d["choices"][0]["message"]["content"]


def optimize_text(text, hits, role="正文"):
    prompt = (
        "以下" + role + "包含敏感词：" + "、".join(hits) + "。"
        "请改写该" + role + "，保留原意与结构，去掉/替换所有敏感表达，"
        "输出改写后的完整文本，不要输出任何其他说明。\n\n" + text
    )
    out = _llm([
        {"role": "system", "content": "你是内容安全编辑，输出仅为改写后的文本。"},
        {"role": "user", "content": prompt},
    ])
    return out.strip()


def log_record(record):
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    data = []
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = []
    record.setdefault("time", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    data.append(record)
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return record


if __name__ == "__main__":
    import sys
    n = load_words()
    print("敏感词库加载完成:", len(n), "词")
    t = sys.argv[1] if len(sys.argv) > 1 else "测试文本"
    print("命中:", check(t))
