"""把 WeWe RSS 拉到的公众号正文整理成独立简报（Markdown + HTML）。

公众号页仍单独输出；与网页采集的合页由 brief_store 另外生成。
不做评论爬取。
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from digest import llm_settings
from wechat import in_news_window, news_window, parse_datetime, window_label

logger = logging.getLogger("wechat_digest")

SKIP_TITLE = ("内推", "招聘", "投融资法务", "讨论群")
JUNK_CUT = ("预览时标签不可点", "留言均为自动精选", "Scan to Follow", "本公众号发布的全部内容仅为")
HIGHLIGHT_TERMS = [
    "OFAC",
    "SDN",
    "BIS",
    "二级制裁",
    "次级制裁",
    "实体清单",
    "出口管制",
    "经济D日",
    "霍尔木兹",
    "影子船队",
    "伊朗",
    "油轮",
    "航运",
]
HEAT_LEGEND = (
    "高：SDN、经济D日、霍尔木兹、二级制裁等直接冲击航运与清单的稿件；"
    "中：OFAC、制裁、实体清单、伊朗油轮等常规执法与合规动态；"
    "低：其余政策解读、诉讼程序、一般合规信息。"
)


def clean_body(text: str) -> str:
    text = text or ""
    for marker in JUNK_CUT:
        idx = text.find(marker)
        if idx > 200:
            text = text[:idx]
    text = re.sub(r"在小说阅读器读本章.*?沉浸阅读", " ", text)
    text = re.sub(r"Original\s+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def should_skip(title: str) -> bool:
    return any(token in (title or "") for token in SKIP_TITLE)


def heat_label(title: str, body: str) -> str:
    blob = f"{title} {body}"
    if any(w in blob for w in ("SDN", "经济D日", "经济驱逐", "霍尔木兹", "二级制裁", "次级制裁")):
        return "高"
    if any(w in blob for w in ("OFAC", "制裁", "实体清单", "油轮", "伊朗")):
        return "中"
    return "低"


def payload_window(payload: dict[str, Any]) -> tuple[datetime, datetime]:
    raw = payload.get("window") or {}
    start = parse_datetime(raw.get("start"))
    end = parse_datetime(raw.get("end"))
    if start and end:
        return start, end
    return news_window()


def article_dt(item: dict[str, Any]) -> datetime | None:
    return parse_datetime(item.get("published_at")) or parse_datetime(item.get("date"))


def pick_articles(
    items: list[dict[str, Any]],
    window: tuple[datetime, datetime],
    limit: int = 24,
) -> list[dict[str, Any]]:
    start, end = window
    scored = []
    for item in items:
        title = item.get("title") or ""
        if should_skip(title):
            continue
        published = article_dt(item)
        if not in_news_window(published, start, end):
            continue
        body = clean_body(item.get("body") or item.get("excerpt") or "")
        if len(body) < 200:
            continue
        heat = heat_label(title, body)
        score = {"高": 30, "中": 18, "低": 6}[heat] + min(len(body) // 400, 8)
        scored.append(
            (
                published.strftime("%Y-%m-%d %H:%M:%S") if published else "",
                score,
                {**item, "body": body, "excerpt": body[:280], "heat": heat},
            )
        )
    scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return [row[2] for row in scored[:limit]]


def theme_hint(n: int) -> str:
    if n <= 3:
        return "1到2个主题"
    if n <= 8:
        return "2到4个主题"
    return "3到5个主题"


def _llm_extract(picked: list[dict[str, Any]]) -> dict[str, Any] | None:
    key, base, model = llm_settings()
    if not key or not picked:
        return None
    payload = []
    for i, item in enumerate(picked, 1):
        payload.append(
            {
                "id": i,
                "title": item.get("title"),
                "source": item.get("source"),
                "text": (item.get("body") or "")[:1400],
            }
        )
    prompt = (
        "你是出口管制与经济制裁研究员。根据材料整理一份中文公众号简报结构。"
        "只输出一个 JSON 对象，不要 Markdown，不要解释。格式：\n"
        "{"
        '"themes":[{"title":"角度小标题","summary":"核心观点，含材料中的数字和名单规模","ids":[1,2]}],'
        '"terms":[{"term":"术语","explain":"不超过40字的解释"}]'
        "}\n"
        f"要求：{theme_hint(len(picked))}，宁可少而清楚，不要硬凑；"
        "每篇文章只归入一个最贴切主题的 ids，禁止一篇多挂；相近稿合并；"
        "主题标题要能一眼看出事件，禁止用“其他”“合规动态”“政策观察”这种空泛名字；"
        "弱相关稿也要单独起能看懂的小标题，并写一句基于正文的摘要；"
        "只用材料事实，禁止编造数字；"
        "terms 5到12个，优先 SDN、OFAC、BIS、二级制裁、实体清单、经济D日等。"
        f"\n材料：\n{json.dumps(payload, ensure_ascii=False)}"
    )
    url = (base.rstrip("/") if base else "https://api.openai.com") + "/v1/chat/completions"
    import httpx

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    body = {
        "model": model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": "只输出合法 JSON。"},
            {"role": "user", "content": prompt},
        ],
    }
    try:
        with httpx.Client(timeout=120.0) as client:
            response = client.post(url, headers=headers, json=body)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"].strip()
        match = re.search(r"\{[\s\S]*\}", content)
        if not match:
            return None
        data = json.loads(match.group(0))
        if not data.get("themes"):
            return None
        return data
    except Exception as exc:  # noqa: BLE001
        logger.warning("公众号 LLM 提炼失败，改用规则分组: %s", exc)
        return None


def fallback_extract(picked: list[dict[str, Any]]) -> dict[str, Any]:
    buckets: dict[str, list[int]] = {
        "对伊制裁与航运网络": [],
        "OFAC / BIS 清单与执法": [],
        "出口管制与实体清单诉讼": [],
        "政策与合规动态": [],
    }
    for i, item in enumerate(picked, 1):
        blob = f"{item.get('title','')} {item.get('body','')}"
        if any(w in blob for w in ("伊朗", "霍尔木兹", "经济D日", "经济驱逐", "油轮")):
            buckets["对伊制裁与航运网络"].append(i)
        elif any(w in blob for w in ("OFAC", "BIS", "SDN", "罚款")):
            buckets["OFAC / BIS 清单与执法"].append(i)
        elif any(w in blob for w in ("实体清单", "诉讼", "法院", "1260H")):
            buckets["出口管制与实体清单诉讼"].append(i)
        else:
            buckets["政策与合规动态"].append(i)
    themes = []
    for title, ids in buckets.items():
        if not ids:
            continue
        titles = [picked[j - 1]["title"] for j in ids[:4]]
        themes.append({"title": title, "summary": "；".join(titles), "ids": ids})
    terms = [
        {"term": "SDN", "explain": "美国特别指定国民与被封锁人员清单"},
        {"term": "OFAC", "explain": "美国财政部海外资产控制办公室"},
        {"term": "BIS", "explain": "美国商务部工业与安全局"},
        {"term": "二级制裁", "explain": "对与受制裁对象交易的第三方施加的限制"},
        {"term": "实体清单", "explain": "美国出口管制下需许可才能交易的外国主体名单"},
    ]
    return {"themes": themes, "terms": terms}


GENERIC_THEME_TITLES = {"其他", "其它", "其他合规动态", "其它合规动态", "合规动态", "政策观察"}


def _story_text(item: dict[str, Any]) -> str:
    title = (item.get("title") or "").strip()
    body = re.sub(r"\s+", " ", (item.get("body") or "").strip())
    if title:
        body = body.replace(title, " ", 1).strip()
    prefix = re.compile(r"^(?:Original|路透午报|路透|图|/)\s*")
    while prefix.match(body):
        body = prefix.sub("", body, count=1).strip()
    body = re.sub(r"^[A-Za-z][A-Za-z .'-]{1,40}\s+", "", body).strip()
    return body


def leftover_blurb(item: dict[str, Any]) -> str:
    """从正文抽出一两句完整摘要，不截断句子。"""
    title = (item.get("title") or "").strip()
    body = _story_text(item)
    parts = [part.strip() for part in re.split(r"(?<=[。！？])", body) if part.strip()]
    chosen: list[str] = []
    total = 0
    for part in parts:
        compact = re.sub(r"\s+", "", part)
        if len(compact) < 12:
            continue
        if title and compact[:10] == re.sub(r"\s+", "", title)[:10]:
            continue
        sentence = part if part.endswith(("。", "！", "？")) else part + "。"
        chosen.append(sentence)
        total += len(sentence)
        if total >= 60 or len(chosen) >= 2:
            break
    text = "".join(chosen)
    if not text:
        return title.rstrip("。") + "。" if title else ""
    if len(text) <= 110:
        return text
    first = chosen[0]
    if len(first) <= 110:
        return first
    cut = first[:108]
    cut = re.sub(r"[，,；;、]\s*\S*$", "", cut)
    return cut.rstrip("，,；;、 ") + "。"


def _llm_brief_summaries(items: list[dict[str, Any]]) -> dict[str, str]:
    """按 url 返回一句完整摘要；失败则空字典。"""
    key, base, model = llm_settings()
    usable = [item for item in items if item.get("url") or item.get("title")]
    if not key or not usable:
        return {}
    payload = []
    for i, item in enumerate(usable, 1):
        payload.append(
            {
                "id": i,
                "title": item.get("title"),
                "text": _story_text(item)[:900],
            }
        )
    prompt = (
        "把下列新闻各写成一句中文简报摘要。只输出 JSON，不要解释。格式："
        '{"summaries":[{"id":1,"summary":"..."}]}。'
        "要求：每条 40 到 90 字；必须是完整句子，以句号结尾；保留关键数字和主体；"
        "不要重复标题；不要截断；不要编造。"
        f"\n材料：\n{json.dumps(payload, ensure_ascii=False)}"
    )
    url = (base.rstrip("/") if base else "https://api.openai.com") + "/v1/chat/completions"
    import httpx

    body = {
        "model": model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": "只输出合法 JSON。"},
            {"role": "user", "content": prompt},
        ],
    }
    try:
        with httpx.Client(timeout=60.0) as client:
            response = client.post(
                url,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=body,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"].strip()
        match = re.search(r"\{[\s\S]*\}", content)
        if not match:
            return {}
        data = json.loads(match.group(0))
        by_id = {
            int(row["id"]): str(row.get("summary") or "").strip()
            for row in (data.get("summaries") or [])
            if isinstance(row, dict) and row.get("id")
        }
        out: dict[str, str] = {}
        for i, item in enumerate(usable, 1):
            summary = by_id.get(i) or ""
            if summary and not summary.endswith(("。", "！", "？")):
                summary = summary.rstrip("，,；; ") + "。"
            if 20 <= len(summary) <= 140:
                out[str(item.get("url") or item.get("title"))] = summary
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("弱相关稿摘要失败，改用正文抽句: %s", exc)
        return {}


def leftover_themes(picked: list[dict[str, Any]], leftovers: list[int]) -> list[dict[str, Any]]:
    buckets: dict[str, list[int]] = {}
    for idx in leftovers:
        item = picked[idx - 1]
        blob = f"{item.get('title', '')} {item.get('body', '')}"
        if any(w in blob for w in ("H-1B", "签证", "移民")):
            key = "美国签证与人员政策"
        elif any(w in blob for w in ("暗杀", "以色列", "真主党")):
            key = "中东安全与外交冲突"
        elif any(w in blob for w in ("悟空", "乐园", "影视", "文娱")):
            key = "国际文娱与社会动态"
        else:
            key = (item.get("title") or "未命名稿件").strip()[:28]
        buckets.setdefault(key, []).append(idx)
    themes = []
    for title, ids in buckets.items():
        ids_items = [picked[j - 1] for j in ids]
        if len(ids) == 1:
            item = ids_items[0]
            title = (item.get("title") or title).strip()[:40]
            summary = leftover_blurb(item)
        else:
            bits = [leftover_blurb(item) for item in ids_items[:3]]
            summary = "；".join(bit for bit in bits if bit)
        themes.append({"title": title, "summary": summary, "ids": ids, "_leftover": True})
    fill_leftover_summaries(picked, themes)
    return themes


def assign_topics(picked: list[dict[str, Any]], extract: dict[str, Any]) -> None:
    claimed: set[int] = set()
    cleaned = []
    skipped_ids: list[int] = []
    for theme in extract.get("themes") or []:
        title = str(theme.get("title") or "").strip()
        if title in GENERIC_THEME_TITLES:
            for idx in theme.get("ids") or []:
                if isinstance(idx, int) and 1 <= idx <= len(picked):
                    skipped_ids.append(idx)
            continue
        ids = []
        for idx in theme.get("ids") or []:
            if not isinstance(idx, int) or idx < 1 or idx > len(picked) or idx in claimed:
                continue
            ids.append(idx)
            claimed.add(idx)
            picked[idx - 1]["topic"] = title or "未命名主题"
        if not ids:
            continue
        theme["ids"] = ids
        cleaned.append(theme)
    leftovers = skipped_ids + [i for i in range(1, len(picked) + 1) if i not in claimed]
    leftovers = list(dict.fromkeys(i for i in leftovers if i not in claimed))
    if leftovers:
        extra = leftover_themes(picked, leftovers)
        for theme in extra:
            for idx in theme["ids"]:
                claimed.add(idx)
                picked[idx - 1]["topic"] = theme["title"]
            cleaned.append(theme)
    polish_theme_copy(picked, cleaned)
    extract["themes"] = cleaned


def _summary_incomplete(text: str) -> bool:
    text = (text or "").strip()
    if not text:
        return True
    if "路透午报" in text or "路透 路透" in text or "图 路透" in text:
        return True
    if not text.endswith(("。", "！", "？")):
        return True
    return False


def fill_leftover_summaries(picked: list[dict[str, Any]], themes: list[dict[str, Any]]) -> None:
    need = []
    for theme in themes:
        ids = [i for i in (theme.get("ids") or []) if isinstance(i, int) and 1 <= i <= len(picked)]
        if not ids:
            continue
        if theme.get("_leftover") or _summary_incomplete(str(theme.get("summary") or "")):
            need.append((theme, ids))
    if not need:
        return
    items = [picked[ids[0] - 1] for theme, ids in need if len(ids) == 1]
    llm_map = _llm_brief_summaries(items)
    for theme, ids in need:
        if len(ids) == 1:
            item = picked[ids[0] - 1]
            key = str(item.get("url") or item.get("title"))
            theme["title"] = (item.get("title") or str(theme.get("title") or "")).strip()[:40]
            theme["summary"] = llm_map.get(key) or leftover_blurb(item)
            item["topic"] = theme["title"]
        else:
            theme["summary"] = "；".join(leftover_blurb(picked[j - 1]) for j in ids[:3])
        theme.pop("_leftover", None)


def polish_theme_copy(picked: list[dict[str, Any]], themes: list[dict[str, Any]]) -> None:
    leftover_names = {
        "美国签证与人员政策",
        "中东安全与外交冲突",
        "国际文娱与社会动态",
    }
    for theme in themes:
        title = str(theme.get("title") or "").strip()
        if title in leftover_names or title in GENERIC_THEME_TITLES:
            theme["_leftover"] = True
    fill_leftover_summaries(picked, themes)


def date_header(stamp: str) -> str:
    return f"{stamp} 公众号信息简报"


def render_markdown(
    stamp: str,
    extract: dict[str, Any],
    picked: list[dict[str, Any]],
    window_text: str,
) -> str:
    lines = [date_header(stamp), ""]
    lines.append(f"统计窗口：{window_text}")
    lines.append(f"热度：{HEAT_LEGEND}")
    lines.append("")
    for theme in extract.get("themes") or []:
        lines.append(str(theme.get("title") or "未命名主题"))
        lines.append(str(theme.get("summary") or "").strip())
        lines.append("")
    terms = extract.get("terms") or []
    if terms:
        lines.append("术语")
        bits = [f"{row.get('term')}：{row.get('explain')}" for row in terms if row.get("term")]
        lines.append("；".join(bits))
        lines.append("")
    lines.append("来源篇目")
    for i, item in enumerate(picked, 1):
        when = item.get("published_at") or item.get("date") or ""
        lines.append(
            f"{i}. {item.get('source')}｜{item.get('title')}｜{when}｜热度{item.get('heat')}｜{item.get('topic') or ''}"
        )
    return "\n".join(lines).rstrip() + "\n"


def highlight_html(text: str) -> str:
    escaped = html.escape(text)
    for term in sorted(HIGHLIGHT_TERMS, key=len, reverse=True):
        pattern = re.compile(re.escape(html.escape(term)))
        escaped = pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", escaped)
    return escaped


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def digest_html(stamp: str, extract: dict[str, Any]) -> str:
    parts = []
    for theme in extract.get("themes") or []:
        parts.append("<section class='theme'>")
        parts.append(f"<h2>{highlight_html(str(theme.get('title') or ''))}</h2>")
        summary = str(theme.get("summary") or "").strip()
        if summary:
            parts.append(f"<p>{highlight_html(summary)}</p>")
        parts.append("</section>")
    terms = [row for row in (extract.get("terms") or []) if row.get("term")]
    if terms:
        items = "".join(
            f"<li><b>{esc(row.get('term'))}</b> {esc(row.get('explain'))}</li>" for row in terms
        )
        parts.append(f"<section class='terms'><h2>术语</h2><ul>{items}</ul></section>")
    return "\n".join(parts)


def card_payload(picked: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in picked:
        rows.append(
            {
                "title": item.get("title") or "",
                "url": item.get("url") or "",
                "source": item.get("source") or "",
                "date": item.get("published_at") or item.get("date") or "",
                "excerpt": (item.get("excerpt") or item.get("body") or "")[:280],
                "heat": item.get("heat") or "低",
                "topic": item.get("topic") or "未分类",
                "keywords": item.get("matched_keywords") or [],
            }
        )
    return rows


def render_html(
    stamp: str,
    extract: dict[str, Any],
    picked: list[dict[str, Any]],
    window_text: str,
) -> str:
    high = sum(1 for a in picked if a.get("heat") == "高")
    mid = sum(1 for a in picked if a.get("heat") == "中")
    low = sum(1 for a in picked if a.get("heat") == "低")
    data_json = json.dumps(card_payload(picked), ensure_ascii=False).replace("<", "\\u003c")
    sources = sorted({a.get("source") or "" for a in picked if a.get("source")})
    topics = []
    for theme in extract.get("themes") or []:
        title = str(theme.get("title") or "").strip()
        if title and title not in topics:
            topics.append(title)
    for item in picked:
        title = str(item.get("topic") or "").strip()
        if title and title not in topics:
            topics.append(title)
    source_opts = "".join(f"<option value='{esc(name)}'>{esc(name)}</option>" for name in sources)
    topic_opts = "".join(f"<option value='{esc(name)}'>{esc(name)}</option>" for name in topics)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{esc(date_header(stamp))}</title>
<style>
:root {{
  --bg:#f4f1ea; --paper:#fffcf6; --ink:#1c1916; --muted:#6b645c;
  --line:#ddd4c6; --accent:#8c3b2a; --ok:#2f5d50;
}}
* {{ box-sizing:border-box; }}
html, body {{ margin:0; padding:0; background:var(--bg); color:var(--ink); }}
body {{
  font:16px/1.65 "Source Han Serif SC","Noto Serif SC","Songti SC","SimSun",Georgia,serif;
}}
a {{ color:var(--accent); text-decoration:none; }}
a:hover {{ text-decoration:underline; }}
.wrap {{ max-width:1280px; margin:0 auto; padding:24px 16px 56px; }}
header.hero {{
  display:flex; justify-content:space-between; gap:20px; align-items:flex-end;
  border-bottom:1px solid var(--line); padding-bottom:16px; margin-bottom:16px;
}}
header.hero h1 {{ margin:0 0 6px; font-size:26px; font-weight:650; }}
.kicker {{ color:var(--muted); font-size:13px; }}
.stats {{ display:flex; gap:8px; flex-wrap:wrap; }}
.stat {{
  background:var(--paper); border:1px solid var(--line); min-width:84px;
  padding:8px 12px 7px; text-align:right;
}}
.stat b {{ display:block; font-size:22px; line-height:1.2; }}
.stat span {{ color:var(--muted); font-size:12px; }}
.legend {{
  background:var(--paper); border:1px solid var(--line); padding:12px 14px; margin-bottom:16px;
  font-size:13px; color:#3d3832;
}}
.legend b {{ margin-right:.2em; }}
.legend .row {{ display:flex; flex-direction:column; gap:8px; }}
.legend .row div {{ display:flex; align-items:flex-start; gap:8px; line-height:1.5; }}
.heat {{ display:inline-block; min-width:1.6em; font-size:.72rem;
  border:1px solid var(--line); padding:0 .35em; border-radius:4px; }}
.h-高 {{ color:#8c3b2a; border-color:#e2c4bc; }}
.h-中 {{ color:#7a5a2a; }}
.h-低 {{ color:var(--muted); }}
.panel {{
  background:var(--paper); border:1px solid var(--line); padding:16px 18px 12px; margin-bottom:18px;
}}
.panel h2, .col-title {{
  margin:0 0 12px; font-size:15px; letter-spacing:.06em; color:var(--muted); font-weight:650;
}}
.compare {{
  display:grid; grid-template-columns:1.05fr .95fr; gap:18px; align-items:stretch;
  height:min(72vh, 820px);
}}
.compare > .col.panel {{
  display:flex; flex-direction:column; height:100%; min-height:0; margin-bottom:0; overflow:hidden;
}}
.col-body {{
  flex:1; min-height:0; overflow-y:auto; padding-right:8px; overscroll-behavior:contain;
}}
.col-body::-webkit-scrollbar {{ width:8px; }}
.col-body::-webkit-scrollbar-thumb {{ background:#d7cbb8; }}
.col-body::-webkit-scrollbar-track {{ background:#eee7db; }}
.digest .theme {{
  background:#fff; border:1px solid var(--line); border-left:4px solid var(--accent);
  padding:12px 14px 14px; margin:0 0 14px;
}}
.digest .theme h2 {{
  font:650 1.05rem/1.4 "Source Han Sans SC","Noto Sans SC",sans-serif;
  color:var(--accent); margin:0 0 10px; padding-bottom:8px;
  border-bottom:1px dashed var(--line); letter-spacing:0;
}}
.digest .theme p {{ margin:0; color:#3a342e; }}
mark {{ background:#f3d9b0; padding:0 .1em; }}
.terms {{ margin-top:8px; }}
.terms ul {{ margin:0; padding-left:1.1em; }}
.terms li {{ margin:0 0 6px; font-size:.92rem; color:#3a342e; }}
.toolbar {{
  display:grid; grid-template-columns:1fr 1fr; gap:8px;
  margin-bottom:12px; flex-shrink:0;
}}
.toolbar input, .toolbar select {{
  width:100%; min-width:0;
  font:14px/1.4 "Source Han Sans SC","Noto Sans SC",sans-serif;
  border:1px solid var(--line); background:#fff; padding:6px 8px; color:var(--ink);
}}
.card {{ border:1px solid var(--line); padding:12px 13px 10px; margin-bottom:10px; background:#fff; }}
.card h3 {{ margin:0 0 6px; font-size:15px; font-family:"Source Han Sans SC","Noto Sans SC",sans-serif; }}
.meta {{ color:var(--muted); font-size:12px; display:flex; flex-wrap:wrap; gap:8px; margin-bottom:6px; align-items:center; }}
.excerpt {{ font-size:13px; color:#3d3832; margin:0; }}
.empty {{ color:var(--muted); }}
@media (max-width:980px) {{
  header.hero {{ flex-direction:column; align-items:flex-start; }}
  .compare {{ grid-template-columns:1fr; height:auto; }}
  .compare > .col.panel {{ height:min(60vh, 640px); margin-bottom:18px; }}
}}
</style>
</head>
<body>
<div class="wrap">
<header class="hero">
  <div>
    <h1>{esc(date_header(stamp))}</h1>
  </div>
  <div class="stats">
    <div class="stat"><b>{len(picked)}</b><span>入选</span></div>
    <div class="stat"><b>{high}</b><span>热度高</span></div>
    <div class="stat"><b>{mid}</b><span>热度中</span></div>
    <div class="stat"><b>{low}</b><span>热度低</span></div>
  </div>
</header>
<section class="legend">
  <div class="row">
    <div><b class="heat h-高">高</b> SDN、经济D日、霍尔木兹、二级制裁等直接冲击航运与清单的稿件</div>
    <div><b class="heat h-中">中</b> OFAC、制裁、实体清单、伊朗油轮等常规执法与合规动态</div>
    <div><b class="heat h-低">低</b> 其余政策解读、诉讼程序、一般合规信息</div>
  </div>
</section>
<div class="compare">
  <div class="col panel">
    <h2 class="col-title">简报</h2>
    <div class="digest col-body">
      {digest_html(stamp, extract)}
    </div>
  </div>
  <div class="col panel">
    <h2 class="col-title">信息源 <span id="shown-count"></span></h2>
    <div class="toolbar">
      <input id="q" type="search" placeholder="搜索标题 / 关键词">
      <select id="source"><option value="">全部来源</option>{source_opts}</select>
      <select id="topic"><option value="">全部主题</option>{topic_opts}</select>
      <select id="heat">
        <option value="">热度不限</option>
        <option value="高">高</option>
        <option value="中">中</option>
        <option value="低">低</option>
      </select>
    </div>
    <div class="col-body" id="cards"></div>
  </div>
</div>
</div>
<script id="payload" type="application/json">{data_json}</script>
<script>
const data = JSON.parse(document.getElementById("payload").textContent);
const cards = document.getElementById("cards");
const shown = document.getElementById("shown-count");
function esc(value) {{
  return String(value ?? "").replace(/[&<>"']/g, function (ch) {{
    if (ch === "&") return "&amp;";
    if (ch === "<") return "&lt;";
    if (ch === ">") return "&gt;";
    if (ch === '"') return "&quot;";
    return "&#39;";
  }});
}}
function matches(item) {{
  const q = document.getElementById("q").value.trim().toLowerCase();
  const source = document.getElementById("source").value;
  const topic = document.getElementById("topic").value;
  const heat = document.getElementById("heat").value;
  if (source && item.source !== source) return false;
  if (topic && item.topic !== topic) return false;
  if (heat && item.heat !== heat) return false;
  if (!q) return true;
  const blob = [item.title, item.excerpt, item.source, item.topic, (item.keywords || []).join(" ")].join(" ").toLowerCase();
  return blob.includes(q);
}}
function render() {{
  const items = data.filter(matches);
  shown.textContent = "（" + items.length + "/" + data.length + "）";
  cards.innerHTML = items.map(item => {{
    return "<article class='card'><h3><a href='" + esc(item.url) + "' target='_blank' rel='noopener'>" + esc(item.title) + "</a></h3>"
      + "<div class='meta'><span class='heat h-" + esc(item.heat) + "'>" + esc(item.heat) + "</span>"
      + "<span>" + esc(item.source) + "</span><span>" + esc(item.topic) + "</span><span>" + esc(item.date || "") + "</span></div>"
      + (item.excerpt ? "<p class='excerpt'>" + esc(item.excerpt) + "</p>" : "") + "</article>";
  }}).join("") || "<p class='empty'>没有符合筛选的条目。</p>";
}}
["q","source","topic","heat"].forEach(id => document.getElementById(id).addEventListener("input", render));
render();
</script>
</body>
</html>
"""


def write_outputs(
    stamp: str,
    markdown: str,
    page: str,
    extract: dict[str, Any],
    picked: list[dict[str, Any]],
    window_text: str,
) -> tuple[Path, Path]:
    import brief_store

    brief_store.migrate_legacy()
    brief_store.wechat_dir(stamp).mkdir(parents=True, exist_ok=True)
    md_path = brief_store.wechat_md(stamp)
    html_path = brief_store.wechat_html(stamp)
    md_path.write_text(markdown, encoding="utf-8")
    html_path.write_text(page, encoding="utf-8")
    analysis_path = brief_store.wechat_analysis(stamp)
    analysis_path.write_text(
        json.dumps(
            {
                "date": stamp,
                "window": window_text,
                "extract": extract,
                "picked": [
                    {
                        "title": a.get("title"),
                        "source": a.get("source"),
                        "url": a.get("url"),
                        "heat": a.get("heat"),
                        "topic": a.get("topic"),
                        "published_at": a.get("published_at") or a.get("date"),
                    }
                    for a in picked
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    brief_store.publish_day(stamp)
    return md_path, html_path


def rebuild_from_saved(stamp: str) -> tuple[Path, Path]:
    """用已有 analysis.json 重写 HTML/Markdown，不再调用模型。"""
    import copy
    import brief_store

    payload = json.loads(brief_store.wechat_json(stamp).read_text(encoding="utf-8"))
    analysis = json.loads(brief_store.wechat_analysis(stamp).read_text(encoding="utf-8"))
    by_url = {a.get("url"): a for a in payload.get("articles") or []}
    picked: list[dict[str, Any]] = []
    for row in analysis.get("picked") or []:
        src = by_url.get(row.get("url")) or {}
        body = clean_body(src.get("body") or src.get("excerpt") or row.get("title") or "")
        item = {**src, **row, "body": body, "excerpt": body[:280]}
        if not item.get("heat"):
            item["heat"] = heat_label(item.get("title") or "", body)
        picked.append(item)
    extract = copy.deepcopy(analysis.get("extract") or {})
    assign_topics(picked, extract)
    window_text = analysis.get("window") or (payload.get("window") or {}).get("label") or ""
    markdown = render_markdown(stamp, extract, picked, window_text)
    page = render_html(stamp, extract, picked, window_text)
    return write_outputs(stamp, markdown, page, extract, picked, window_text)


def build_from_json(json_path: str | Path) -> tuple[Path, Path]:
    path = Path(json_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    stamp = payload.get("date") or datetime.now().strftime("%Y-%m-%d")
    window = payload_window(payload)
    picked = pick_articles(payload.get("articles") or [], window)
    extract = _llm_extract(picked) or fallback_extract(picked)
    assign_topics(picked, extract)
    window_text = (payload.get("window") or {}).get("label") or window_label(*window)
    markdown = render_markdown(stamp, extract, picked, window_text)
    page = render_html(stamp, extract, picked, window_text)
    return write_outputs(stamp, markdown, page, extract, picked, window_text)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="根据 wechat-日期.json 生成公众号简报")
    parser.add_argument("--from-json", default="", help="日期或 briefs/日期/wechat/articles.json")
    parser.add_argument("--reuse-analysis", action="store_true", help="用已有 analysis.json 重写页面，不调用模型")
    args = parser.parse_args()
    import brief_store

    if args.reuse_analysis:
        stamp = args.from_json or datetime.now().strftime("%Y-%m-%d")
        md_path, html_path = rebuild_from_saved(stamp)
    else:
        json_path = str(brief_store.resolve_wechat_json(args.from_json))
        md_path, html_path = build_from_json(json_path)
    print(md_path)
    print(html_path)


if __name__ == "__main__":
    main()
