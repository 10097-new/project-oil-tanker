"""跨日期行业知识库：去重、合并、持久化。

同一事件只保留信息最全、来源最权威的一篇；其余挂「相关阅读」。
已入库条目不再出现在后续日期的日报里。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import config
from taxonomy import authority_score

TITLE_STOP = {"the", "a", "an", "of", "to", "for", "and", "in", "on", "at", "after", "from", "with", "via"}


def library_dir() -> Path:
    path = config.LIBRARY_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def library_json() -> Path:
    return library_dir() / config.LIBRARY_JSON


def alerts_json() -> Path:
    return library_dir() / config.ALERTS_JSON


def knowledge_html() -> Path:
    return library_dir() / "index.html"


def canonical_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url.strip())
    path = (parsed.path or "/").rstrip("/") or "/"
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return urlunparse((parsed.scheme.lower() or "https", host, path, "", "", ""))


def title_tokens(title: str) -> set[str]:
    text = (title or "").lower()
    tokens = {
        tok
        for tok in re.findall(r"[a-z0-9]+", text)
        if tok not in TITLE_STOP and len(tok) > 2
    }
    for chunk in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(chunk) <= 4:
            tokens.add(chunk)
            continue
        tokens.update(chunk[i : i + 2] for i in range(len(chunk) - 1))
    return tokens


def titles_similar(a: str, b: str) -> bool:
    ta, tb = title_tokens(a), title_tokens(b)
    if not ta or not tb:
        return False
    overlap = len(ta & tb)
    return overlap >= 4 or overlap / min(len(ta), len(tb)) >= 0.55


def fingerprint(item: dict[str, Any]) -> str:
    url = canonical_url(str(item.get("url") or item.get("canonical_url") or ""))
    if url:
        return "url:" + url
    title = re.sub(r"\W+", " ", str(item.get("title") or "").lower()).strip()
    return "title:" + title[:180]


def substance_score(item: dict[str, Any]) -> int:
    body = item.get("body") or ""
    excerpt = item.get("excerpt") or ""
    auth = authority_score(str(item.get("source_key") or ""))
    return auth * 10 + min(len(body), 4000) + min(len(excerpt), 400)


def load_library() -> list[dict[str, Any]]:
    path = library_json()
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if isinstance(payload, list):
        return payload
    return payload.get("articles") or []


def save_library(articles: list[dict[str, Any]]) -> Path:
    path = library_json()
    payload = {
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(articles),
        "articles": articles,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _index_library(articles: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    by_fp: dict[str, dict[str, Any]] = {}
    for item in articles:
        fp = item.get("fingerprint") or fingerprint(item)
        item["fingerprint"] = fp
        by_fp[fp] = item
    return by_fp, articles


def find_duplicate(item: dict[str, Any], library: list[dict[str, Any]]) -> dict[str, Any] | None:
    fp = item.get("fingerprint") or fingerprint(item)
    item["fingerprint"] = fp
    url_key = canonical_url(str(item.get("url") or ""))
    title = str(item.get("title") or "")
    for existing in library:
        if (existing.get("fingerprint") or fingerprint(existing)) == fp:
            return existing
        if url_key and canonical_url(str(existing.get("url") or "")) == url_key:
            return existing
        if titles_similar(title, str(existing.get("title") or "")):
            return existing
        zh = existing.get("title_zh") or ""
        en = existing.get("title_en") or ""
        if zh and titles_similar(title, zh):
            return existing
        if en and titles_similar(title, en):
            return existing
    return None


def cluster_day(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一事件只留一篇主稿，其余写入 related。"""
    ranked = sorted(items, key=substance_score, reverse=True)
    picked: list[dict[str, Any]] = []
    for item in ranked:
        host: dict[str, Any] | None = None
        for kept in picked:
            if titles_similar(str(item.get("title") or ""), str(kept.get("title") or "")):
                host = kept
                break
            if canonical_url(str(item.get("url") or "")) and canonical_url(str(item.get("url") or "")) == canonical_url(
                str(kept.get("url") or "")
            ):
                host = kept
                break
        if host is None:
            item.setdefault("related", [])
            picked.append(item)
            continue
        related = list(host.get("related") or [])
        related.append(
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "source": item.get("source"),
                "date": item.get("published_at") or item.get("date"),
            }
        )
        host["related"] = related
        if substance_score(item) > substance_score(host):
            # 互换：更权威/更全的升为主稿，原主稿改挂相关阅读
            old_main = {k: v for k, v in host.items() if k != "related"}
            for key, value in item.items():
                if key != "related":
                    host[key] = value
            related.append(
                {
                    "title": old_main.get("title"),
                    "url": old_main.get("url"),
                    "source": old_main.get("source"),
                    "date": old_main.get("published_at") or old_main.get("date"),
                }
            )
            host["related"] = [r for r in related if r.get("url") != host.get("url")]
    return picked


def merge_into_library(
    new_items: list[dict[str, Any]],
    library: list[dict[str, Any]] | None = None,
    stamp: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """返回 (今日新增主稿, 更新后的全库)。已见过的稿不会进入今日新增。"""
    stamp = stamp or datetime.now().strftime("%Y-%m-%d")
    library = list(library if library is not None else load_library())
    clustered = cluster_day(new_items)
    fresh: list[dict[str, Any]] = []
    for item in clustered:
        item["fingerprint"] = fingerprint(item)
        item.setdefault("first_seen", stamp)
        dup = find_duplicate(item, library)
        if dup is not None:
            extra = list(dup.get("related") or [])
            extra.extend(item.get("related") or [])
            extra.append(
                {
                    "title": item.get("title"),
                    "url": item.get("url"),
                    "source": item.get("source"),
                    "date": item.get("published_at") or item.get("date"),
                }
            )
            seen_urls = {dup.get("url")}
            merged = []
            for row in extra:
                url = row.get("url")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                merged.append(row)
            dup["related"] = merged[:8]
            if substance_score(item) > substance_score(dup) and len(item.get("body") or "") > len(dup.get("body") or ""):
                for key in ("excerpt", "excerpt_zh", "excerpt_en", "body", "analysis", "tags", "categories"):
                    if item.get(key):
                        dup[key] = item[key]
            continue
        fresh.append(item)
        library.append(item)
    return fresh, library
