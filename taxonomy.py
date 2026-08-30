"""油轮行业情报：分类、同义词、来源权威度。"""

from __future__ import annotations

from datetime import datetime, timedelta

# 第三步：新闻分类标签（key 用于存储，zh/en 用于界面）
CATEGORIES: list[dict[str, str]] = [
    {"key": "fundamentals", "zh": "油轮基本面", "en": "Tanker Fundamentals"},
    {"key": "fleet", "zh": "运力更新", "en": "Fleet Updates"},
    {"key": "charterer", "zh": "客户/租家动态", "en": "Charterer / Customer"},
    {"key": "peer", "zh": "同行动态", "en": "Peer Moves"},
    {"key": "trade", "zh": "石油贸易", "en": "Oil Trade"},
    {"key": "refinery", "zh": "炼厂终端", "en": "Refinery Downstream"},
    {"key": "geopolitics", "zh": "地缘局势", "en": "Geopolitics"},
    {"key": "policy", "zh": "经贸政策", "en": "Trade & Policy"},
    {"key": "analysis", "zh": "专题分析", "en": "Special Reports"},
    {"key": "non_tanker", "zh": "航运（非油轮）", "en": "Shipping (Non-Tanker)"},
    {"key": "breaking", "zh": "最近24小时", "en": "Last 24 Hours"},
]

CATEGORY_BY_KEY = {item["key"]: item for item in CATEGORIES}
CATEGORY_BY_LABEL = {item["zh"]: item for item in CATEGORIES}
CATEGORY_BY_LABEL.update({item["en"]: item for item in CATEGORIES})

# 来源权威度：语义去重时优先保留分高、信息更全的一篇
SOURCE_AUTHORITY: dict[str, int] = {
    "iea": 100,
    "spglobal": 95,
    "platts": 95,
    "tradewinds": 90,
    "lloydslist": 88,
    "wechat_lloydslist": 88,
    "shippingwatch": 85,
    "wechat_crsl": 85,
    "wechat_kpler": 85,
    "wechat_argus": 85,
    "seatrade": 80,
    "wechat_reuters": 80,
    "splash247": 75,
    "hellenic": 72,
    "bairdmaritime": 70,
    "wechat_broker": 70,
    "wechat_jlc": 68,
    "sol": 65,
    "eworldship": 60,
}

# 检索联想：任一写法命中后，同组词全部参与匹配
SYNONYM_GROUPS: list[list[str]] = [
    ["招商轮船", "招商", "招商局", "招商油轮", "China VLCC", "CMES", "China Merchants Energy Shipping", "招商局能源运输"],
    ["中远海能", "中远海运能源", "COSCO Shipping Energy", "COSCO Energy", "COSCO tanker"],
    ["超大型油轮", "VLCC", "very large crude carrier", "VLCCs", "超大型原油轮"],
    ["苏伊士型", "Suezmax", "suezmaxes"],
    ["阿芙拉型", "Aframax", "aframaxes"],
    ["成品油轮", "product tanker", "LR1", "LR2", "MR tanker", "MR2", "long range"],
    ["化学品船", "chemical tanker", "chem tanker"],
    ["运价", "freight rate", "spot rate", "TCE", "worldscale", "WS", "即期运价", "日租金"],
    ["霍尔木兹", "Hormuz", "Strait of Hormuz", "霍尔木兹海峡"],
    ["红海", "Red Sea", "Bab el-Mandeb", "曼德海峡", "胡塞", "Houthi"],
    ["影子船队", "shadow fleet", "dark fleet", "ghost fleet", "暗影船队"],
    ["制裁", "sanctions", "OFAC", "SDN", "禁运", "embargo", "price cap", "油价上限"],
    ["Frontline", "FRO", "Euronav", "CMB.Tech"],
    ["DHT", "DHT Holdings"],
    ["International Seaways", "INSW", "Seaways"],
    ["Scorpio Tankers", "STNG", "Hafnia", "TORM"],
    ["克拉克森", "Clarksons", "CRSL", "Clarkson Research"],
    ["阿格斯", "Argus", "Argus Media"],
    ["标普", "Platts", "S&P Global", "S&P Commodity Insights"],
    ["IEA", "国际能源署", "International Energy Agency", "石油市场报告", "Oil Market Report"],
    ["炼厂", "refinery", "refineries", "开工率", "utilization", "run rate"],
    ["拆解", "scrapping", "demolition", "recycling"],
    ["新造船", "newbuilding", "newbuild", "orderbook", "订单"],
    ["二手船", "secondhand", "S&P market", "resale"],
    ["租家", "charterer", "charterers", "货主", "oil major"],
    ["原油", "crude", "crude oil", "petroleum"],
    ["成品油", "products", "gasoline", "diesel", "naphtha", "jet fuel"],
    ["干散货", "dry bulk", "bulker", "capesize", "panamax bulk"],
    ["集装箱", "container", "containership", "boxship", "liner"],
]

# 规则分类用的关键词（命中即可打上对应类别；一篇可多类）
CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "fundamentals": [
        "tce", "worldscale", "spot rate", "freight rate", "tanker rate", "earnings",
        "tonne-mile", "ton-mile", "fleet size", "supply-demand", "utilization",
        "运价", "日租金", "吨海里", "供需", "船队规模", "基本面", "即期",
    ],
    "fleet": [
        "newbuild", "newbuilding", "orderbook", "delivery", "delivered", "scrapping",
        "demolition", "secondhand", "s&p", "resale", "new order",
        "新造船", "订单", "交付", "拆解", "二手船", "成交",
    ],
    "charterer": [
        "charterer", "charterers", "fixture", "time charter", "voyage charter",
        "oil major", "aramco", "shell", "bp ", "totalenergies", "unipec", "sinochem",
        "租家", "货主", "租约", "成交", "炼厂采购",
    ],
    "peer": [
        "frontline", "euronav", "dht", "seaways", "scorpio", "torm", "hafnia",
        "cmes", "招商轮船", "中远海能", "owner", "shipowner", "fleet expansion",
        "earnings call", "guidance", "战略", "船东", "运力扩张", "业绩",
    ],
    "trade": [
        "export", "import", "trade flow", "arbitrage", "crude flow", "loadings",
        "discharge", "barrels", "b/d", "bpd",
        "出口", "进口", "贸易流", "套利", "装货", "卸货",
    ],
    "refinery": [
        "refinery", "refineries", "run rate", "throughput", "turnaround", "outage",
        "restart", "cdus", "crack spread",
        "炼厂", "开工率", "停产", "复产", "检修", "成品油供需",
    ],
    "geopolitics": [
        "hormuz", "red sea", "houthi", "black sea", "ukraine", "iran", "russia",
        "strait", "attack", "missile", "blockade", "conflict",
        "霍尔木兹", "红海", "胡塞", "黑海", "俄乌", "袭击", "封锁", "冲突", "中东",
    ],
    "policy": [
        "sanction", "ofac", "tariff", "carbon", "ets", "imo", "fueleu", "cape",
        "embargo", "price cap", "regulation", "port policy",
        "制裁", "关税", "碳税", "航运法规", "港口政策", "限硫",
    ],
    "analysis": [
        "outlook", "forecast", "white paper", "market report", "research",
        "trend", "analysis", "briefing",
        "展望", "研判", "深度", "报告", "趋势", "机构观点",
    ],
    "non_tanker": [
        "bulk carrier", "bulker", "capesize", "containership", "container ship",
        "boxship", "lng carrier", "lpg carrier", "dry bulk", "liner",
        "干散货", "集装箱", "散货船", "箱船",
    ],
}


def category_label(key: str, lang: str = "zh") -> str:
    item = CATEGORY_BY_KEY.get(key)
    if not item:
        return key
    return item["zh"] if lang == "zh" else item["en"]


def authority_score(source_key: str) -> int:
    return SOURCE_AUTHORITY.get(source_key, 50)


def synonym_index() -> dict[str, list[str]]:
    """词 → 同组全部写法（含自身）。"""
    index: dict[str, list[str]] = {}
    for group in SYNONYM_GROUPS:
        for word in group:
            index[word.lower()] = group
    return index


def expand_query(text: str) -> list[str]:
    """把检索词扩成同义写法，供模糊匹配。"""
    blob = (text or "").strip()
    if not blob:
        return []
    found: list[str] = [blob]
    lowered = blob.lower()
    for group in SYNONYM_GROUPS:
        if any(term.lower() in lowered or lowered in term.lower() for term in group):
            found.extend(group)
    # 保序去重
    seen: set[str] = set()
    out: list[str] = []
    for item in found:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def rule_categories(text: str, published_at: str | None = None, now: datetime | None = None) -> list[str]:
    blob = (text or "").lower()
    keys: list[str] = []
    for key, words in CATEGORY_KEYWORDS.items():
        if any(word.lower() in blob for word in words):
            keys.append(key)
    now = now or datetime.now()
    if _is_last_24h(published_at, now) and "breaking" not in keys:
        keys.append("breaking")
    if not keys:
        keys.append("fundamentals")
    return keys[:5]


def _is_last_24h(published_at: str | None, now: datetime) -> bool:
    if not published_at:
        return False
    text = published_at.strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text[: len(fmt) + 2].strip(), fmt) if fmt != "%Y-%m-%d" else datetime.strptime(text[:10], fmt)
            return now - dt <= timedelta(hours=24)
        except ValueError:
            continue
    return False


def rule_tags(text: str, limit: int = 5) -> list[str]:
    """从同义组与分类词里抽 3–5 个标签。"""
    blob = (text or "").lower()
    tags: list[str] = []
    for group in SYNONYM_GROUPS:
        canonical = group[0]
        if any(term.lower() in blob for term in group):
            if canonical not in tags:
                tags.append(canonical)
        if len(tags) >= limit:
            return tags
    extra = ["VLCC", "运价", "制裁", "霍尔木兹", "原油", "炼厂", "新造船", "租家"]
    for word in extra:
        if word.lower() in blob and word not in tags:
            tags.append(word)
        if len(tags) >= limit:
            break
    return tags[:limit]
