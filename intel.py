"""油轮行业情报处理：摘要、分类、双语、去重入库、日报。"""

from __future__ import annotations

import argparse
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import config
from digest import llm_settings, skip_story
from library import cluster_day, find_duplicate, load_library, merge_into_library, save_library
from taxonomy import category_label, expand_query, rule_categories, rule_tags

logger = logging.getLogger("intel")

ZH_RE = re.compile(r"[\u4e00-\u9fff]")


def detect_language(text: str) -> str:
    if not text:
        return "en"
    zh = len(ZH_RE.findall(text))
    return "zh" if zh >= max(8, len(text) * 0.08) else "en"


def clip_summary(text: str, limit: int = 150) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    cut = re.sub(r"[，,；;、]\s*\S*$", "", cut)
    return cut.rstrip("，,；;、 ") + "…"


def normalize_item(raw: dict[str, Any], channel: str) -> dict[str, Any] | None:
    title = str(raw.get("title") or "").strip()
    url = str(raw.get("url") or "").strip()
    if not title or not url or skip_story(title):
        return None
    body = str(raw.get("body") or "")
    excerpt = str(raw.get("excerpt") or "")
    published = str(raw.get("published_at") or raw.get("date") or "").strip()
    date = published[:10] if published else None
    time_str = published[:16] if len(published) >= 16 else (f"{date} 00:00" if date else "")
    blob = " ".join(part for part in (title, excerpt, body) if part)
    language = str(raw.get("language") or detect_language(title + excerpt))
    categories = raw.get("categories") or rule_categories(blob, time_str)
    tags = raw.get("tags") or rule_tags(blob)
    item = {
        "title": title,
        "title_zh": raw.get("title_zh") or (title if language == "zh" else ""),
        "title_en": raw.get("title_en") or (title if language == "en" else ""),
        "url": url,
        "source": raw.get("source") or "",
        "source_key": raw.get("source_key") or "",
        "group": channel,
        "date": date,
        "published_at": time_str or date,
        "excerpt": clip_summary(excerpt or body or title),
        "excerpt_zh": raw.get("excerpt_zh") or "",
        "excerpt_en": raw.get("excerpt_en") or "",
        "body": body,
        "categories": categories,
        "tags": tags[:5],
        "language": language,
        "translated": bool(raw.get("translated")),
        "related": raw.get("related") or [],
        "analysis": raw.get("analysis") or "",
        "matched_keywords": raw.get("matched_keywords") or [],
    }
    if language == "zh" and not item["excerpt_zh"]:
        item["excerpt_zh"] = item["excerpt"]
    if language == "en" and not item["excerpt_en"]:
        item["excerpt_en"] = item["excerpt"]
    return item


def load_day_raw(stamp: str) -> list[dict[str, Any]]:
    import brief_store

    items: list[dict[str, Any]] = []
    web_path = brief_store.web_json(stamp)
    if web_path.exists():
        payload = json.loads(web_path.read_text(encoding="utf-8"))
        for row in payload.get("articles") or []:
            item = normalize_item(row, "web")
            if item:
                items.append(item)
    wx_path = brief_store.wechat_json(stamp)
    if wx_path.exists():
        payload = json.loads(wx_path.read_text(encoding="utf-8"))
        for row in payload.get("articles") or []:
            item = normalize_item(row, "wechat")
            if item:
                items.append(item)
    return items


def _llm_enrich(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    key, base, model = llm_settings()
    if not key or not items:
        return items
    payload = []
    for i, item in enumerate(items, 1):
        payload.append(
            {
                "id": i,
                "title": item.get("title"),
                "source": item.get("source"),
                "date": item.get("published_at") or item.get("date"),
                "lang": item.get("language"),
                "text": ((item.get("body") or item.get("excerpt") or "")[:1600]),
            }
        )
    cats = "、".join(c["zh"] for c in __import__("taxonomy").CATEGORIES if c["key"] != "breaking")
    prompt = (
        "你是资深油轮行业新闻分析师，服务公司高层。不是新闻搬运工，而是行业情报官。\n"
        "对每条材料输出结构化字段。只输出 JSON，不要解释。格式：\n"
        '{"items":[{"id":1,"title_zh":"中文标题","title_en":"English title",'
        '"summary_zh":"150字以内中文摘要，含核心事实与关键数据",'
        '"summary_en":"English summary under 400 characters",'
        '"analysis_zh":"一句话：意味着什么（趋势/风险/对油轮市场的含义）",'
        '"categories":["油轮基本面"],"tags":["VLCC","运价"],'
        '"translated":true}]}\n'
        "分类只能从下列选取 1-3 个：" + cats + "。\n"
        "标签 3-5 个关键词。准确性优先于时效性，禁止编造数字。"
        "保持中立。已翻译的中文标题/摘要 translated=true。\n"
        f"材料：\n{json.dumps(payload, ensure_ascii=False)}"
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
        with httpx.Client(timeout=180.0) as client:
            response = client.post(
                url,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=body,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"].strip()
        match = re.search(r"\{[\s\S]*\}", content)
        if not match:
            return items
        data = json.loads(match.group(0))
        by_id = {
            int(row["id"]): row
            for row in (data.get("items") or [])
            if isinstance(row, dict) and row.get("id")
        }
        from taxonomy import CATEGORY_BY_LABEL

        for i, item in enumerate(items, 1):
            row = by_id.get(i) or {}
            if row.get("title_zh"):
                item["title_zh"] = str(row["title_zh"]).strip()
            if row.get("title_en"):
                item["title_en"] = str(row["title_en"]).strip()
            if row.get("summary_zh"):
                item["excerpt_zh"] = clip_summary(str(row["summary_zh"]))
            if row.get("summary_en"):
                item["excerpt_en"] = clip_summary(str(row["summary_en"]), 400)
            if row.get("analysis_zh"):
                item["analysis"] = str(row["analysis_zh"]).strip()
            cats_in = row.get("categories") or []
            keys = []
            for label in cats_in:
                found = CATEGORY_BY_LABEL.get(str(label))
                if found and found["key"] not in keys:
                    keys.append(found["key"])
            if keys:
                if "breaking" in (item.get("categories") or []) and "breaking" not in keys:
                    keys.append("breaking")
                item["categories"] = keys[:5]
            tags = [str(t).strip() for t in (row.get("tags") or []) if str(t).strip()]
            if tags:
                item["tags"] = tags[:5]
            if row.get("translated"):
                item["translated"] = True
            elif item.get("language") == "en" and item.get("excerpt_zh"):
                item["translated"] = True
            item["excerpt"] = item.get("excerpt_zh") or item.get("excerpt") or item["excerpt"]
        return items
    except Exception as exc:  # noqa: BLE001
        logger.warning("情报结构化失败，改用规则摘要: %s", exc)
        return items


def enrich_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not items:
        return items
    batch_size = 12
    out: list[dict[str, Any]] = []
    for start in range(0, len(items), batch_size):
        chunk = items[start : start + batch_size]
        out.extend(_llm_enrich(chunk))
    return out


def render_item_md(item: dict[str, Any], lang: str = "zh") -> str:
    title = item.get("title_zh") if lang == "zh" else item.get("title_en")
    title = title or item.get("title")
    summary = item.get("excerpt_zh") if lang == "zh" else item.get("excerpt_en")
    summary = summary or item.get("excerpt") or ""
    trans = "（译）" if item.get("translated") and lang == "zh" else ""
    tags = "、".join(item.get("tags") or [])
    cats = "、".join(category_label(k, lang) for k in (item.get("categories") or []))
    lines = [
        f"【标题】{title}{trans}",
        f"【时间】{item.get('published_at') or item.get('date') or ''}",
        f"【来源】{item.get('source')}",
        f"【摘要】{summary}",
        f"【链接】{item.get('url')}",
        f"【标签】{tags or cats}",
    ]
    if item.get("analysis"):
        lines.append(f"【研判】{item['analysis']}")
    related = item.get("related") or []
    if related:
        bits = [f"{r.get('source') or ''} {r.get('title') or ''} {r.get('url') or ''}".strip() for r in related[:5]]
        lines.append("【相关阅读】" + "；".join(bits))
    return "\n".join(lines)


def render_daily_markdown(stamp: str, items: list[dict[str, Any]], lang: str = "zh") -> str:
    header = f"{stamp} 油轮行业情报" if lang == "zh" else f"{stamp} Tanker Industry Intelligence"
    lines = [
        header,
        "",
        "来源权威、数据准确、分析有深度。同一事件只保留最全面的一篇，其余见相关阅读。跨日期不重复入库。",
        "",
        f"本日新增 **{len(items)}** 条。",
        "",
    ]
    by_cat: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        keys = item.get("categories") or ["fundamentals"]
        by_cat.setdefault(keys[0], []).append(item)
    order = [c["key"] for c in __import__("taxonomy").CATEGORIES]
    for key in order:
        group = by_cat.get(key) or []
        if not group:
            continue
        lines.append(f"## {category_label(key, lang)}")
        lines.append("")
        for item in group:
            lines.append(render_item_md(item, lang))
            lines.append("")
    leftover = [i for i in items if (i.get("categories") or ["fundamentals"])[0] not in order]
    if leftover:
        lines.append("## 其他")
        lines.append("")
        for item in leftover:
            lines.append(render_item_md(item, lang))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def save_day_intel(stamp: str, items: list[dict[str, Any]]) -> Path:
    import brief_store

    brief_store.stamp_dir(stamp).mkdir(parents=True, exist_ok=True)
    md_path = brief_store.intel_md(stamp)
    md_path.write_text(render_daily_markdown(stamp, items), encoding="utf-8")
    json_path = brief_store.intel_json(stamp)
    json_path.write_text(
        json.dumps({"date": stamp, "count": len(items), "articles": items}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    html_path = brief_store.intel_html(stamp)
    html_path.write_text(render_daily_html(stamp, items), encoding="utf-8")
    return md_path


def render_daily_html(stamp: str, items: list[dict[str, Any]]) -> str:
    from knowledge import esc

    cards = []
    for item in items:
        trans = "<span class='badge'>译</span>" if item.get("translated") else ""
        tags = " ".join(f"<span class='pill'>{esc(t)}</span>" for t in (item.get("tags") or []))
        related = item.get("related") or []
        rel = ""
        if related:
            links = "；".join(
                f"<a href='{esc(r.get('url'))}' target='_blank' rel='noopener'>{esc(r.get('source') or '')} {esc(r.get('title') or '')}</a>"
                for r in related[:5]
            )
            rel = f"<p class='related'>相关阅读：{links}</p>"
        analysis = f"<p class='analysis'>研判：{esc(item.get('analysis'))}</p>" if item.get("analysis") else ""
        cards.append(
            f"""<article class="card">
            <h2><a href="{esc(item.get('url'))}" target="_blank" rel="noopener">{esc(item.get('title_zh') or item.get('title'))}</a> {trans}</h2>
            <div class="meta"><span>{esc(item.get('source'))}</span><span>{esc(item.get('published_at') or item.get('date'))}</span></div>
            <p>{esc(item.get('excerpt_zh') or item.get('excerpt'))}</p>
            {analysis}
            <div class="pills">{tags}</div>
            {rel}
            </article>"""
        )
    body = "".join(cards) or "<p class='empty'>本日无新增条目（可能均已在知识库中出现过）。</p>"
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{stamp} 油轮行业情报</title>
<style>
:root {{ --bg:#f4f1ea; --paper:#fffcf6; --ink:#1c1916; --muted:#6b645c; --line:#ddd4c6; --accent:#8c3b2a; }}
body {{ margin:0; background:var(--bg); color:var(--ink);
  font:16px/1.65 "Source Han Serif SC","Noto Serif SC","Songti SC",Georgia,serif; }}
.wrap {{ max-width:860px; margin:0 auto; padding:24px 16px 64px; }}
a {{ color:var(--accent); }}
.card {{ background:#fff; border:1px solid var(--line); padding:14px 16px; margin:0 0 12px; }}
.meta {{ color:var(--muted); font-size:12px; display:flex; gap:8px; margin:0 0 8px; }}
.badge {{ font-size:11px; border:1px solid #e2c4bc; color:var(--accent); padding:0 5px; }}
.pill {{ font-size:12px; background:#f0e9dc; padding:2px 8px; margin-right:4px; }}
.related, .analysis {{ font-size:14px; color:#3d3832; }}
.empty {{ color:var(--muted); }}
.nav {{ font-size:13px; margin:0 0 16px; }}
</style>
</head>
<body>
<div class="wrap">
  <p class="nav"><a href="../library/index.html">知识库</a></p>
  <h1>{stamp} 油轮行业情报</h1>
  <p>本日新增 {len(items)} 条。同一事件只保留最全面的一篇。</p>
  {body}
</div>
</body>
</html>
"""


def process_day(stamp: str | None = None) -> dict[str, Any]:
    stamp = stamp or datetime.now().strftime("%Y-%m-%d")
    raw = load_day_raw(stamp)
    clustered = cluster_day(raw)
    library = load_library()
    pending = [item for item in clustered if find_duplicate(item, library) is None]
    logger.info("原始 %s 条，去重后待入库 %s 条", len(raw), len(pending))
    enriched = enrich_items(pending)
    fresh, library = merge_into_library(enriched, library, stamp=stamp)
    save_library(library)
    today_items = [item for item in library if item.get("first_seen") == stamp]
    save_day_intel(stamp, today_items)
    from knowledge import write_knowledge_html

    html_path = write_knowledge_html(library)
    from notify import dispatch_alerts

    alerts = dispatch_alerts(fresh, stamp)
    import brief_store

    brief_store.publish_day(stamp)
    logger.info("本日新增 %s 条，知识库共 %s 条", len(fresh), len(library))
    return {
        "stamp": stamp,
        "raw": len(raw),
        "fresh": len(fresh),
        "today": len(today_items),
        "library": len(library),
        "html": str(html_path),
        "alerts": alerts,
    }


def matches_query(item: dict[str, Any], query: str) -> bool:
    if not query:
        return True
    terms = expand_query(query)
    blob = " ".join(
        str(item.get(k) or "")
        for k in ("title", "title_zh", "title_en", "excerpt", "excerpt_zh", "excerpt_en", "source")
    )
    blob += " " + " ".join(item.get("tags") or [])
    blob_l = blob.lower()
    return any(term.lower() in blob_l for term in terms)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="把当日网页/公众号采集结果整理进油轮行业知识库")
    parser.add_argument("--date", default="", help="日期 YYYY-MM-DD，默认今天")
    args = parser.parse_args()
    result = process_day(args.date or None)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
