"""把采集结果整理成 Oil Digest 叙事简报。"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from typing import Any

import config

logger = logging.getLogger("daily_brief")

MONTHS_EN = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]

# 长词优先，用于把英文标题/要点转成中文简报用语
PHRASES: list[tuple[str, str]] = [
    ("strait of hormuz", "霍尔木兹海峡"),
    ("bab el-mandeb", "曼德海峡"),
    ("bab el mandeb", "曼德海峡"),
    ("red sea", "红海"),
    ("northern red sea", "红海北部"),
    ("shadow fleet", "影子船队"),
    ("dark fleet", "暗影船队"),
    ("secondary sanctions", "二级制裁"),
    ("press release", "新闻稿"),
    ("shuttle tankers", "穿梭油轮"),
    ("shuttle tanker", "穿梭油轮"),
    ("to asia", "运往亚洲"),
    ("asia", "亚洲"),
    ("mediterranean sea", "地中海"),
    ("mediterranean", "地中海"),
    ("oil tanker", "油轮"),
    ("crude oil", "原油"),
    ("oil price", "油价"),
    ("oil export", "原油出口"),
    ("oil exports", "原油出口"),
    ("shipping", "航运"),
    ("sanctions relief", "制裁豁免"),
    ("economic d-day", "经济D日"),
    ("united states", "美国"),
    ("u.s. department of the treasury", "美国财政部"),
    ("treasury", "美国财政部"),
    ("ofac", "OFAC"),
    ("iranian regime", "伊朗政权"),
    ("iranian", "伊朗"),
    ("iran", "伊朗"),
    ("iraq", "伊拉克"),
    ("iraqi", "伊拉克"),
    ("saudi arabia", "沙特阿拉伯"),
    ("saudi", "沙特"),
    ("venezuela", "委内瑞拉"),
    ("houthis", "胡塞武装"),
    ("houthi", "胡塞武装"),
    ("china", "中国"),
    ("chinese", "中国"),
    ("russia", "俄罗斯"),
    ("russian", "俄罗斯"),
    ("syria", "叙利亚"),
    ("syrian", "叙利亚"),
    ("ukraine", "乌克兰"),
    ("trump", "特朗普"),
    ("bessent", "贝森特"),
    ("yanbu", "延布"),
    ("ain sukhna", "阿因苏赫纳"),
    ("sidi kerir", "西迪克里尔"),
    ("baghdād", "巴格达"),
    ("baghdad", "巴格达"),
    ("authorised", "授权"),
    ("authorized", "授权"),
    ("service fees", "服务费"),
    ("service fee", "服务费"),
    ("transiting", "过境"),
    ("transit", "过境"),
    ("blockade", "封锁"),
    ("sanctions", "制裁"),
    ("sanction", "制裁"),
    ("tanker", "油轮"),
    ("tankers", "油轮"),
    ("vessels", "船舶"),
    ("vessel", "船舶"),
    ("barrels a day", "桶/日"),
    ("barrels per day", "桶/日"),
    ("million barrels", "百万桶"),
    ("oil", "石油"),
    ("crude", "原油"),
    ("warns", "警告"),
    ("approves", "批准"),
    ("allows", "批准"),
    ("launches", "启动"),
    ("unprecedented", "空前"),
    ("campaign", "行动"),
    ("against", "针对"),
    ("through", "经由"),
    ("safer", "更安全的"),
    ("route", "航线"),
    ("evade", "规避"),
    ("attacks", "袭击"),
    ("traffic", "通航量"),
    ("falls", "下降"),
    ("below", "低于"),
    ("ships", "船舶"),
    ("weekend", "周末"),
    ("amid", "在…背景下"),
    ("requests from", "应…请求"),
    ("after requests from", "应…请求"),
    ("select", "部分"),
    ("fines", "罚款"),
    ("detention", "扣押"),
    ("confiscation", "没收"),
    ("violating", "违反"),
    ("rules", "规则"),
    ("for", ""),
    ("the", ""),
    ("of", ""),
    ("to", ""),
    ("and", "与"),
    ("in", ""),
    ("on", ""),
    ("a", ""),
    ("an", ""),
]


def load_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(config.ROOT / ".env")
    except Exception:
        return


def llm_settings() -> tuple[str | None, str | None, str]:
    load_env()
    key = (
        os.getenv("DIGEST_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("DEEPSEEK_API_KEY")
        or os.getenv("DASHSCOPE_API_KEY")
    )
    base = os.getenv("DIGEST_BASE_URL") or os.getenv("OPENAI_BASE_URL")
    model = os.getenv("DIGEST_MODEL") or ""
    if os.getenv("DEEPSEEK_API_KEY") and not os.getenv("DIGEST_API_KEY") and not os.getenv("OPENAI_API_KEY"):
        base = base or "https://api.deepseek.com"
        model = model or "deepseek-chat"
    if os.getenv("DASHSCOPE_API_KEY") and not model:
        base = base or "https://dashscope.aliyuncs.com/compatible-mode"
        model = "qwen-plus"
    return key, base, model or "gpt-4o-mini"


def skip_story(title: str) -> bool:
    lowered = title.lower()
    return any(token in lowered for token in config.SKIP_TITLE_KEYWORDS)


def title_tokens(title: str) -> set[str]:
    stop = {"the", "a", "an", "of", "to", "for", "and", "in", "on", "at", "after", "from", "with", "via"}
    return {tok for tok in re.findall(r"[a-z0-9\u4e00-\u9fff]+", title.lower()) if tok not in stop and len(tok) > 2}


def similar(a: str, b: str) -> bool:
    ta, tb = title_tokens(a), title_tokens(b)
    if not ta or not tb:
        return False
    overlap = len(ta & tb)
    return overlap >= 4 or overlap / min(len(ta), len(tb)) >= 0.55


def story_score(article: Any, today: str) -> int:
    title = article.title.lower()
    score = 0
    themes = set(getattr(article, "themes", []) or [])
    if "oil_transport" in themes and "sanctions" in themes:
        score += 6
    if getattr(article, "group", "") == "official":
        score += 3
        if "iran" in title:
            score += 6
    if "economic d-day" in title or "outcast" in title:
        score += 10
    if "sanction" in title and "iran" in title:
        score += 4
    for word, pts in (
        ("hormuz", 4),
        ("iran", 3),
        ("sanction", 3),
        ("tanker", 2),
        ("crude", 2),
        ("oil", 1),
        ("houthi", 2),
        ("shadow", 2),
        ("price", 2),
        ("export", 2),
        ("iraq", 2),
        ("saudi", 2),
        ("venezuela", 2),
    ):
        if word in title:
            score += pts
    if getattr(article, "date", None) in {today, _yesterday(today)}:
        score += 2
    return score


def _yesterday(today: str) -> str:
    try:
        dt = datetime.strptime(today, "%Y-%m-%d")
        return datetime.fromordinal(dt.toordinal() - 1).strftime("%Y-%m-%d")
    except ValueError:
        return today


def cluster_and_rank(articles: list[Any], limit: int) -> list[Any]:
    today = datetime.now().strftime("%Y-%m-%d")
    ranked = sorted(
        [a for a in articles if not skip_story(a.title)],
        key=lambda a: story_score(a, today),
        reverse=True,
    )
    picked: list[Any] = []
    for article in ranked:
        if any(similar(article.title, kept.title) for kept in picked):
            # 把更长正文并入已选条目
            for kept in picked:
                if similar(article.title, kept.title):
                    extra = getattr(article, "body", "") or article.excerpt
                    current = getattr(kept, "body", "") or kept.excerpt
                    if extra and len(extra) > len(current or ""):
                        kept.body = extra
                    break
            continue
        picked.append(article)
        if limit and len(picked) >= limit:
            break
    return picked


def clean_source_text(text: str) -> str:
    if not text:
        return ""
    if "<" in text:
        from bs4 import BeautifulSoup

        text = BeautifulSoup(text, "html.parser").get_text(" ", strip=True)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


TITLE_HINTS = [
    ("economic d-day", "美国财政部对伊朗启动空前“经济D日”制裁行动"),
    ("economic outcast", "美国财政部对伊朗启动空前“经济D日”制裁行动"),
    ("service fees", "伊朗将对来自“授权”国家的过境霍尔木兹海峡船只收取服务费"),
    ("iraqi oil tankers", "伊朗批准伊拉克油轮通过霍尔木兹海峡"),
    ("northern red sea", "船东将沙特原油改道更安全的北方航线以规避胡塞袭击"),
    ("yanbu", "船东将沙特原油改道更安全的北方航线以规避胡塞袭击"),
    ("below 20", "霍尔木兹海峡周末通航量降至不足20艘"),
    ("sanctions relief on syria", "美国宣布进一步调整叙利亚制裁并更新伊朗相关名单"),
    ("russian oil tanker", "英国突击队扣押俄罗斯油轮，印度籍船长被捕"),
    ("warns vessels", "伊朗警告违规过境霍尔木兹海峡的船舶将面临罚款、扣押和没收"),
]


THEME_RULES: list[tuple[str, str, tuple[str, ...]]] = [
    ("iran_hormuz", "伊朗制裁与霍尔木兹通航", (
        "hormuz", "economic d-day", "economic outcast", "iran sanction", "iranian",
        "service fee", "iraqi", "blacklist", "blockade", "outcast",
    )),
    ("red_sea", "红海胡塞与沙特原油改道", (
        "houthi", "red sea", "yanbu", "bahri", "ain sukhna", "northern red",
    )),
    ("tanker_market", "油轮运价与过境成本", (
        "vlcc", "spot rate", "tce", "freight", "tanker rate", "$20 million",
        "charter", "earnings",
    )),
    ("shadow_enforcement", "影子船队、海盗与制裁执法", (
        "shadow", "dark fleet", "hijack", "somalia", "aden", "captain",
        "pirate", "piracy", "price cap",
    )),
    ("official_other", "其他官方制裁与合规", (
        "syria", "ofac", "ofsi", "restrictive measure", "eu council",
    )),
]

STOCK_NOISE = (
    "nasdaq",
    "nvidia",
    "s&p 500",
    "s&p500",
    "dow jones futures",
    "dow jones today",
    "wall street",
    "chip stocks",
    "fed outlook",
    "blue chips",
    "stock market today",
)
CHROME_MARKERS = (
    "organizational chart",
    "top 10 reasons to work here",
    "role of the treasury",
    "inspector general audits",
    "total views:",
)
GENERIC_FILLER = "该消息与石油运输或经济制裁相关"


def specialized_body(article: Any, title_zh: str = "") -> str | None:
    blob = f"{article.title} {getattr(article, 'body', '') or article.excerpt or ''}"
    low = blob.lower()
    if "economic d-day" in low or "economic outcast" in low:
        return (
            "美国财政部启动针对伊朗航运与石油贸易的经济施压行动，并将此轮举措称为“经济D日”。"
            "制裁范围覆盖协助运送伊朗原油的网络，并警告在霍尔木兹海峡配合伊方通航要求可能面临额外制裁风险。"
        )
    if "service fee" in low and "hormuz" in low:
        return (
            "伊朗宣布对获“授权”国家的船只过境霍尔木兹海峡收取服务费，覆盖导航、环境支持、保险等项目。"
        )
    if "iraqi" in low and "hormuz" in low:
        return (
            "伊朗已批准部分伊拉克油轮通过霍尔木兹海峡。伊拉克原油出口高度依赖该水道，并在评估替代出口路线。"
        )
    if "yanbu" in low or ("saudi" in low and "houthi" in low and "tanker" in low):
        return (
            "为规避胡塞武装对红海南部出口通道的袭击，船东协助沙特将原油改走更安全的北方路线，油轮从延布北送。"
        )
    if "hormuz" in low and ("traffic" in low or "below 20" in low):
        return None
    if "syria" in low and "sanction" in low and "relief" in low:
        return (
            "美国财政部与国务院宣布进一步调整叙利亚相关制裁，并同步更新伊朗相关指定名单和通用许可证。"
            "此举属于叙利亚国家支持恐怖主义名单调整后的后续安排，可能改变相关航运与金融合规口径。"
        )
    if "captain" in low and "tanker" in low:
        return (
            "英国军方扣押一艘与俄罗斯原油运输相关的油轮后，船上印度籍船长被捕，其家属称他被当作替罪羊。"
            "该案处于对俄石油贸易、影子船队和制裁执法趋严的背景下，可能影响相关航线和船员配员安排。"
        )
    if "hijack" in low or ("somalia" in low and "aden" in low):
        return (
            "一艘受美国制裁的成品油轮在亚丁湾遭武装分子劫持并改驶索马里。"
            "英国海事贸易行动中心称事发于也门穆卡拉以东约136海里，约6名武装人员登船控制船舶，该水域本周已出现多起类似报告。"
        )
    if "20 million" in low and ("vlcc" in low or "hormuz" in low):
        return (
            "道达尔能源首席执行官表示，一艘VLCC经霍尔木兹海峡的运输成本已升至约2000万美元，显示海峡风险溢价急剧抬升。"
        )
    return None


def extract_numeric_bits(text: str, limit: int = 8) -> list[str]:
    """抽出带数字的句子/片段，供简报保留运价、艘次、桶/日等事实。"""
    blob = re.sub(r"\s+", " ", text or "").strip()
    if not blob:
        return []
    parts = re.split(r"(?<=[。．.!?;；])\s+", blob)
    if len(parts) == 1:
        parts = re.split(r"(?<=\.)\s+", blob)
    bits: list[str] = []
    seen: set[str] = set()
    for part in parts:
        chunk = part.strip(" -|")
        if len(chunk) < 18 or not re.search(r"\d", chunk):
            continue
        key = chunk.lower()[:80]
        if key in seen:
            continue
        seen.add(key)
        bits.append(chunk[:280])
        if len(bits) >= limit:
            break
    return bits


def usable_text(article: Any) -> str:
    title = (article.title or "").strip()
    excerpt = clean_source_text(getattr(article, "excerpt", "") or "")
    body = clean_source_text(getattr(article, "body", "") or "")
    low_body = body.lower()
    if any(marker in low_body for marker in CHROME_MARKERS):
        body = ""
    if excerpt and title and excerpt.lower().startswith(title.lower()[:50]) and len(excerpt) < len(title) + 80:
        excerpt = ""
    return " ".join(part for part in (body, excerpt) if part).strip()


def has_substance(article: Any) -> bool:
    title = (article.title or "").strip()
    if not title or skip_story(title):
        return False
    low = title.lower()
    if any(token in low for token in STOCK_NOISE):
        return False
    if specialized_body(article):
        return True
    text = usable_text(article)
    if GENERIC_FILLER in text:
        return False
    if extract_numeric_bits(f"{title} {text}", limit=1):
        return True
    if len(text) >= 160:
        return True
    if re.search(r"\d", title) and len(title) >= 40:
        return True
    specific = (
        "hijack", "blacklist", "hormuz", "yanbu", "vlcc", "piracy", "pirate",
        "service fee", "economic d-day", "shadow fleet", "houthi", "ofac",
    )
    return any(token in low for token in specific) and len(title) >= 28


def assign_theme(article: Any) -> tuple[str, str]:
    blob = f"{article.title} {usable_text(article)}".lower()
    for key, label, tokens in THEME_RULES:
        if any(token in blob for token in tokens):
            return key, label
    return "other", "其他油运与制裁动态"


def hint_title(title: str) -> str | None:
    low = title.lower()
    for hint, zh in TITLE_HINTS:
        if hint in low:
            return zh
    return None


def theme_body(items: list[Any], label: str) -> str:
    paragraphs: list[str] = []
    seen: set[str] = set()
    for article in items:
        text = usable_text(article)
        facts = extract_numeric_bits(f"{article.title}. {text}", limit=5)
        if facts:
            source = getattr(article, "source", "相关媒体")
            block = f"{source}指出，" + "；".join(facts) + "。"
            key = block[:100]
            if key not in seen:
                seen.add(key)
                paragraphs.append(block)
            continue
        special = specialized_body(article)
        if special and special not in seen and not re.search(r"\d", " ".join(paragraphs)):
            # 仅在尚无数字事实时用定性综述补全
            seen.add(special)
            paragraphs.append(special)
            continue
        if len(text) >= 120:
            snippet = text[:420].rstrip(" 。;,. ")
            if snippet not in seen:
                seen.add(snippet)
                source = getattr(article, "source", "相关媒体")
                paragraphs.append(f"{source}：{snippet}。")
    if not paragraphs:
        return ""
    return "".join(p if p.endswith(("。", "！", "？")) else p + "。" for p in paragraphs[:5])


def select_for_digest(articles: list[Any]) -> list[Any]:
    usable = [a for a in articles if has_substance(a)]
    return cluster_and_rank(usable, 0)


def group_by_theme(articles: list[Any]) -> list[dict[str, str]]:
    buckets: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for article in articles:
        key, label = assign_theme(article)
        if key not in buckets:
            buckets[key] = {"title": label, "items": [], "score": 0}
            order.append(key)
        buckets[key]["items"].append(article)
        today = datetime.now().strftime("%Y-%m-%d")
        buckets[key]["score"] += story_score(article, today)
    stories = []
    ranked_keys = sorted(order, key=lambda k: buckets[k]["score"], reverse=True)
    for key in ranked_keys:
        bucket = buckets[key]
        body = theme_body(bucket["items"], bucket["title"])
        if not body or GENERIC_FILLER in body:
            continue
        stories.append({"title": bucket["title"], "body": body[:1400]})
        if len(stories) >= getattr(config, "DIGEST_THEME_LIMIT", 8):
            break
    return stories


def digest_date_line(now: datetime | None = None) -> str:
    now = now or datetime.now()
    return f"{now.year}年{now.month}月{now.day}日 油轮行业情报"


def render_oil_digest(lead_title: str, lead_body: str, stories: list[dict[str, str]]) -> str:
    lines = [digest_date_line(), ""]
    for story in stories:
        lines.extend([story["title"], story["body"], ""])
    return "\n".join(lines).rstrip() + "\n"


def fallback_digest(articles: list[Any]) -> str:
    picked = select_for_digest(articles)
    stories = group_by_theme(picked)
    if not stories:
        return digest_date_line() + "\n\n本日暂无足够具体的石油运输或经济制裁事实可写入简报。\n"
    return render_oil_digest(stories[0]["title"], stories[0]["body"], stories)


def _llm_prompt(picked: list[Any]) -> str:
    payload = []
    for i, article in enumerate(picked, 1):
        text = usable_text(article)
        blob = f"{article.title}. {text}"
        payload.append(
            {
                "id": i,
                "title": article.title,
                "source": article.source,
                "date": article.date,
                "facts": extract_numeric_bits(blob, limit=10),
                "text": text[:1800] or article.title,
            }
        )
    header = digest_date_line()
    return (
        "你是资深油轮行业新闻分析师，服务公司高层，不是新闻搬运工。\n"
        "不要分成刊头综述和“每日简报”两套内容，那是重复结构。只要一份：\n"
        f"第一行必须是：{header}\n"
        "空一行后，按主题连续写。每个主题先独占一行标题，下一行写综述。主题之间空一行。\n"
        "不要写 Oil Digest，不要写“每日简报”，不要要点列表，不要链接，不要Markdown#标题。\n"
        "写法要求：\n"
        "1. 把相近报道合并成4到8个主题，优先覆盖：油轮基本面、运力更新、租家/同行动态、石油贸易、炼厂、地缘、经贸政策。\n"
        "2. 风格要具体：优先写入材料中的数量、金额、运价、艘次、吨位、桶/日、百分比、海里、日期、船名。材料里有数字就必须用；没有数字的主题写短一点，或删掉。\n"
        "3. 只使用本次材料JSON里的事实。禁止编造。每条有出处。不仅写发生了什么，还要写意味着什么。\n"
        "4. 全中文；保持中立客观。准确性优先于时效性。\n"
        f"材料JSON：\n{json.dumps(payload, ensure_ascii=False)}"
    )


def llm_digest(articles: list[Any]) -> str | None:
    key, base, model = llm_settings()
    if not key:
        return None
    picked = select_for_digest(articles)
    if not picked:
        return None
    url = (base.rstrip("/") if base else "https://api.openai.com") + "/v1/chat/completions"
    import httpx

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    body = {
        "model": model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": "你只输出简报正文，不要解释。"},
            {"role": "user", "content": _llm_prompt(picked)},
        ],
    }
    try:
        with httpx.Client(timeout=120.0) as client:
            response = client.post(url, headers=headers, json=body)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"].strip()
        if "每日简报" in content or content.startswith("Oil Digest"):
            logger.warning("LLM 仍输出了旧的双层结构，改用主题模板")
            return None
        if digest_date_line().split()[0][:4] not in content[:80]:
            content = digest_date_line() + "\n\n" + content
        return content if content.endswith("\n") else content + "\n"
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM 简报失败，改用主题模板: %s", exc)
        return None


def build_oil_digest(articles: list[Any]) -> str:
    text = llm_digest(articles)
    if text:
        return text
    return fallback_digest(articles)

