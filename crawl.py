"""
油轮行业新闻采集器

抓取来源清单中的网页（Platts、IEA、ShippingWatch 等），筛选油轮及相关航运条目，
生成 Markdown + JSON。跨日期去重与结构化情报由 intel.py 完成。

用法:
  python crawl.py
  python crawl.py --days 3 --deep
  python crawl.py --wechat
  python wechat.py
  python intel.py
  python crawl.py --schedule --at 12:10
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import re
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from dateutil import parser as date_parser

import config
from digest import build_oil_digest, cluster_and_rank, load_env
from sources import SOURCES, Source

try:
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "未安装 crawl4ai。请先执行:\n"
        "  pip install -r requirements.txt\n"
        "  python -m playwright install chromium\n"
        f"原始错误: {exc}"
    ) from exc


MD_LINK_RE = re.compile(r"\[([^\]]{12,240})\]\((https?://[^\s\)]+)\)")
ISO_DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
WORD_RE_CACHE: dict[str, re.Pattern[str]] = {}

logger = logging.getLogger("daily_brief")


@dataclass
class Article:
    title: str
    url: str
    source: str
    source_key: str
    group: str
    date: str | None = None
    published_at: str | None = None
    excerpt: str = ""
    body: str = ""
    themes: list[str] = field(default_factory=list)
    matched_keywords: list[str] = field(default_factory=list)
    page_url: str = ""
    language: str = "en"


@dataclass
class SourceStatus:
    key: str
    name_zh: str
    ok: bool
    count: int = 0
    kept: int = 0
    error: str = ""
    note: str = ""


def setup_logging() -> None:
    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = config.LOGS_DIR / f"crawl_{datetime.now():%Y-%m-%d}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def keyword_pattern(keyword: str) -> re.Pattern[str]:
    cached = WORD_RE_CACHE.get(keyword)
    if cached:
        return cached
    if re.search(r"[\u4e00-\u9fff]", keyword):
        pattern = re.compile(re.escape(keyword), re.I)
    elif re.fullmatch(r"[a-z0-9/\-]+", keyword, re.I):
        pattern = re.compile(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", re.I)
    else:
        pattern = re.compile(re.escape(keyword), re.I)
    WORD_RE_CACHE[keyword] = pattern
    return pattern


def find_keywords(text: str, keywords: list[str]) -> list[str]:
    haystack = text or ""
    hits: list[str] = []
    for keyword in keywords:
        if keyword_pattern(keyword).search(haystack):
            hits.append(keyword)
    return hits


def classify(text: str) -> tuple[list[str], list[str]]:
    oil_hits = find_keywords(text, config.OIL_TRANSPORT_KEYWORDS)
    sanction_hits = find_keywords(text, config.SANCTIONS_KEYWORDS)
    themes: list[str] = []
    if oil_hits:
        themes.append("oil_transport")
    if sanction_hits:
        themes.append("sanctions")
    return themes, oil_hits + sanction_hits


def geo_maritime_hits(text: str) -> list[str]:
    geo = find_keywords(text, config.GEO_KEYWORDS)
    if not geo:
        return []
    maritime = find_keywords(text, config.MARITIME_CONTEXT_KEYWORDS)
    if not maritime:
        return []
    return geo + maritime


def parse_datetime_value(value: str | None) -> datetime | None:
    if not value:
        return None
    text = re.sub(r"\s+", " ", value).strip()
    if not text:
        return None
    try:
        dt = date_parser.parse(text, fuzzy=True, default=datetime(1900, 1, 1))
        if dt.year < 2000:
            return None
        return dt
    except (ValueError, OverflowError, TypeError):
        iso = ISO_DATE_RE.search(text)
        if iso:
            try:
                return datetime.strptime(iso.group(1), "%Y-%m-%d")
            except ValueError:
                return None
        return None


def parse_date(value: str | None) -> str | None:
    dt = parse_datetime_value(value)
    return dt.strftime("%Y-%m-%d") if dt else None


def format_published_at(value: str | None) -> str | None:
    dt = parse_datetime_value(value)
    if not dt:
        return None
    if dt.hour or dt.minute:
        return dt.strftime("%Y-%m-%d %H:%M")
    return dt.strftime("%Y-%m-%d 00:00")


def within_lookback(date_str: str | None, days: int) -> bool:
    if not date_str:
        return True
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return True
    return dt >= (datetime.now().date() - timedelta(days=days))


def normalize_url(href: str, base_url: str) -> str:
    url = urljoin(base_url, href.strip())
    parsed = urlparse(url)
    if not parsed.scheme.startswith("http"):
        return ""
    return parsed._replace(fragment="").geturl()


def domain_allowed(url: str, allowed: list[str]) -> bool:
    if not allowed:
        return True
    host = urlparse(url).netloc.lower()
    return any(host == d or host.endswith("." + d) or d in host for d in allowed)


def clean_text(value: str | None, limit: int = 400) -> str:
    text = value or ""
    if "<" in text:
        text = BeautifulSoup(text, "html.parser").get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text


def is_noise_title(title: str) -> bool:
    stripped = title.strip()
    lowered = stripped.lower()
    if lowered in config.NAV_TITLE_BLOCKLIST:
        return True
    if stripped.startswith("!") or stripped.startswith("[!"):
        return True
    if len(stripped) < 18:
        return True
    return False


def is_image_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return path.endswith(IMAGE_EXT) or "/wp-content/uploads/" in path


def is_blocked_url(url: str) -> bool:
    lowered = url.lower()
    if is_image_url(url):
        return True
    return any(frag in lowered for frag in config.URL_BLOCK_FRAGMENTS)


def result_html(result: Any) -> str:
    return result.html or result.cleaned_html or ""


def result_markdown(result: Any) -> str:
    md = getattr(result, "markdown", None)
    if md is None:
        return ""
    if isinstance(md, str):
        return md
    return getattr(md, "raw_markdown", None) or str(md)


def looks_like_login(html: str, n_articles: int) -> bool:
    if n_articles > 0:
        return False
    text = html.lower()
    return sum(hint in text for hint in config.LOGIN_HINTS) >= 1


def first_match(node, selectors: list[str]):
    for selector in selectors:
        found = node.select_one(selector)
        if found:
            return found
    return None


def soup_of(html: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")


def extract_by_selectors(html: str, source: Source, page_url: str) -> list[Article]:
    soup = soup_of(html)
    articles: list[Article] = []
    seen: set[str] = set()

    items: list = []
    for selector in source.item_selectors:
        items.extend(soup.select(selector))
        if items:
            break

    for item in items:
        title_node = first_match(item, source.title_selectors) if source.title_selectors else None
        link_node = first_match(item, source.link_selectors) if source.link_selectors else title_node
        if title_node is None and link_node is not None:
            title_node = link_node
        if title_node is None:
            continue

        title = clean_text(title_node.get_text(" ", strip=True), 240)
        href = ""
        if link_node is not None:
            href = link_node.get("href") or ""
        if not href and title_node.has_attr("href"):
            href = title_node.get("href") or ""
        url = normalize_url(href, page_url)
        if not title or not url or is_noise_title(title) or is_blocked_url(url):
            continue
        if not domain_allowed(url, source.allowed_domains):
            continue
        if url in seen:
            continue
        seen.add(url)

        date_node = first_match(item, source.date_selectors) if source.date_selectors else None
        date_text = ""
        if date_node is not None:
            date_text = date_node.get("datetime") or date_node.get_text(" ", strip=True)
        excerpt_node = first_match(item, source.excerpt_selectors) if source.excerpt_selectors else None
        excerpt = clean_text(excerpt_node.get_text(" ", strip=True) if excerpt_node else "", 280)

        articles.append(
            Article(
                title=title,
                url=url,
                source=source.name_zh,
                source_key=source.key,
                group=source.group,
                date=parse_date(date_text),
                published_at=format_published_at(date_text),
                excerpt=excerpt,
                page_url=page_url,
                language=getattr(source, "language", "en"),
            )
        )
        if len(articles) >= source.max_items:
            break
    return articles


def extract_markdown_links(markdown: str, source: Source, page_url: str) -> list[Article]:
    articles: list[Article] = []
    seen: set[str] = set()
    for title, href in MD_LINK_RE.findall(markdown or ""):
        title = clean_text(title, 240)
        url = normalize_url(href, page_url)
        if not title or not url or is_noise_title(title) or is_blocked_url(url):
            continue
        if not domain_allowed(url, source.allowed_domains):
            continue
        if url in seen:
            continue
        seen.add(url)
        articles.append(
            Article(
                title=title,
                url=url,
                source=source.name_zh,
                source_key=source.key,
                group=source.group,
                page_url=page_url,
                language=getattr(source, "language", "en"),
            )
        )
        if len(articles) >= source.max_items:
            break
    return articles


def extract_json_ld(html: str, source: Source, page_url: str) -> list[Article]:
    soup = soup_of(html)
    articles: list[Article] = []
    seen: set[str] = set()
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        nodes = data if isinstance(data, list) else [data]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            graph = node.get("@graph", [node])
            if not isinstance(graph, list):
                graph = [graph]
            for item in graph:
                if not isinstance(item, dict):
                    continue
                types = item.get("@type")
                types = types if isinstance(types, list) else [types]
                if not any(t in {"NewsArticle", "Article", "BlogPosting"} for t in types):
                    continue
                title = clean_text(str(item.get("headline") or item.get("name") or ""), 240)
                url = normalize_url(str(item.get("url") or ""), page_url)
                if not title or not url or url in seen:
                    continue
                seen.add(url)
                articles.append(
                    Article(
                        title=title,
                        url=url,
                        source=source.name_zh,
                        source_key=source.key,
                        group=source.group,
                        date=parse_date(str(item.get("datePublished") or item.get("dateModified") or "")),
                        published_at=format_published_at(str(item.get("datePublished") or item.get("dateModified") or "")),
                        excerpt=clean_text(str(item.get("description") or ""), 280),
                        page_url=page_url,
                        language=getattr(source, "language", "en"),
                    )
                )
    return articles


def unwrap_google_news_url(url: str, item=None) -> str:
    """尽量把 Google News 包装链接还原成原文地址。"""
    if not url or "news.google.com" not in url:
        return url
    if item is not None:
        desc = item.find("description") or item.find("summary")
        if desc is not None:
            inner = BeautifulSoup(desc.get_text(" ", strip=True) or str(desc), "html.parser")
            for anchor in inner.find_all("a", href=True):
                href = (anchor.get("href") or "").strip()
                if href.startswith("http") and "news.google.com" not in href:
                    return href
    try:
        encoded = url.split("/articles/")[1].split("?")[0]
        padded = encoded + "=" * (-len(encoded) % 4)
        decoded = base64.urlsafe_b64decode(padded)
        match = re.search(rb"https?://[^\x00-\x20\"'<>]+", decoded)
        if match:
            found = match.group(0).decode("utf-8", errors="ignore").rstrip(".,);")
            if found.startswith("http") and "news.google.com" not in found:
                return found
    except (IndexError, ValueError):
        pass
    return url


def extract_rss(xml_text: str, source: Source, page_url: str) -> list[Article]:
    soup = BeautifulSoup(xml_text, "xml")
    items = soup.find_all("item") or soup.find_all("entry")
    if not items:
        soup = soup_of(xml_text)
        items = soup.find_all("item") or soup.find_all("entry")
    articles: list[Article] = []
    seen: set[str] = set()
    for item in items:
        title = clean_text(item.find("title").get_text(" ", strip=True) if item.find("title") else "", 240)
        link_node = item.find("link")
        href = ""
        if link_node is not None:
            href = (link_node.get_text(" ", strip=True) or link_node.get("href") or "").strip()
        if not href and item.find("guid") is not None:
            href = item.find("guid").get_text(" ", strip=True)
        href = unwrap_google_news_url(href, item)
        url = normalize_url(href, page_url)
        if not title or not url or is_noise_title(title) or is_blocked_url(url):
            continue
        from_google_news = "news.google.com" in (page_url or "")
        if not from_google_news and not domain_allowed(url, source.allowed_domains):
            continue
        if url in seen:
            continue
        seen.add(url)
        date_text = ""
        for tag in ("pubDate", "published", "updated", "dc:date"):
            node = item.find(tag)
            if node is not None:
                date_text = node.get_text(" ", strip=True)
                break
        excerpt_node = item.find("description") or item.find("summary")
        articles.append(
            Article(
                title=title,
                url=url,
                source=source.name_zh,
                source_key=source.key,
                group=source.group,
                date=parse_date(date_text),
                published_at=format_published_at(date_text),
                excerpt=clean_text(excerpt_node.get_text(" ", strip=True) if excerpt_node else "", 280),
                page_url=page_url,
                language=getattr(source, "language", "en"),
            )
        )
        if len(articles) >= source.max_items:
            break
    return articles


def merge_articles(groups: list[list[Article]], limit: int) -> list[Article]:
    merged: list[Article] = []
    seen: set[str] = set()
    for group in groups:
        for article in group:
            key = article.url.rstrip("/").lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(article)
            if len(merged) >= limit:
                return merged
    return merged


def article_should_keep(article: Article, source: Source, days: int) -> bool:
    title_blob = " ".join(filter(None, [article.title, article.url]))
    full_blob = " ".join(filter(None, [article.title, article.excerpt, article.url]))
    blob = title_blob if source.group == "official" else full_blob
    lowered_title = (article.title or "").lower()
    if any(token in lowered_title for token in config.SKIP_TITLE_KEYWORDS):
        return False

    oil_hits = find_keywords(blob, config.OIL_TRANSPORT_KEYWORDS)
    sanction_hits = find_keywords(blob, config.SANCTIONS_KEYWORDS)
    extra_hits = find_keywords(blob, source.extra_keywords) if source.extra_keywords else []
    geo_hits = geo_maritime_hits(title_blob)
    non_tanker_hits = find_keywords(blob, config.NON_TANKER_SHIPPING_KEYWORDS)
    maritime_hits = find_keywords(blob, config.MARITIME_CONTEXT_KEYWORDS)

    keep = bool(oil_hits or sanction_hits or extra_hits or geo_hits or non_tanker_hits)
    if not keep and getattr(source, "keep_broad", False) and maritime_hits:
        keep = True
    if not keep:
        return False
    if not within_lookback(article.date, days):
        return False

    themes: list[str] = []
    if oil_hits:
        themes.append("oil_transport")
    if sanction_hits or geo_hits:
        themes.append("sanctions")
    if non_tanker_hits and "oil_transport" not in themes:
        themes.append("non_tanker")
    if not themes:
        themes.append("oil_transport")

    article.themes = sorted(set(themes))
    article.matched_keywords = sorted(
        set(oil_hits + sanction_hits + extra_hits + geo_hits + non_tanker_hits)
    )[:12]
    article.language = getattr(source, "language", article.language)
    return True


def make_run_config(source: Source) -> CrawlerRunConfig:
    kwargs: dict[str, Any] = {
        "cache_mode": CacheMode.BYPASS,
        "page_timeout": config.CRAWL_TIMEOUT_MS,
        "delay_before_return_html": config.CRAWL_DELAY_SEC,
        "wait_until": "domcontentloaded",
        "remove_overlay_elements": True,
        "exclude_external_images": True,
    }
    if source.wait_for:
        kwargs["wait_for"] = source.wait_for
    if source.key in {"spglobal", "iea", "tradewinds", "shippingwatch", "seatrade"}:
        kwargs["wait_until"] = "networkidle"
        kwargs["delay_before_return_html"] = 4.0
        kwargs["page_timeout"] = 120_000
    if source.key in {"bairdmaritime", "sol", "eworldship", "hellenic"}:
        kwargs["magic"] = True
        kwargs["wait_until"] = "load"
        kwargs["delay_before_return_html"] = 8.0
        kwargs["page_timeout"] = 90_000
    kwargs["simulate_user"] = True
    kwargs["override_navigator"] = True
    for _ in range(8):
        try:
            return CrawlerRunConfig(**kwargs)
        except TypeError as exc:
            msg = str(exc)
            dropped = False
            for key in list(kwargs):
                if f"'{key}'" in msg or f"{key}" in msg:
                    kwargs.pop(key, None)
                    dropped = True
                    break
            if not dropped:
                kwargs.pop("wait_until", None)
                kwargs.pop("exclude_external_images", None)
                break
    return CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        page_timeout=kwargs.get("page_timeout", config.CRAWL_TIMEOUT_MS),
        delay_before_return_html=kwargs.get("delay_before_return_html", config.CRAWL_DELAY_SEC),
        remove_overlay_elements=True,
    )


class FallbackResult:
    def __init__(self, url: str, html: str, success: bool = True, error_message: str = ""):
        self.url = url
        self.html = html
        self.cleaned_html = html
        self.markdown = ""
        self.success = success
        self.error_message = error_message


async def fetch_html_http(url: str) -> str:
    import httpx

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    async with httpx.AsyncClient(follow_redirects=True, timeout=40.0, headers=headers) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text


def html_looks_empty(html: str) -> bool:
    if not html or len(html) < 800:
        return True
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    return len(text) < 80


async def crawl_page(crawler: AsyncWebCrawler, url: str, source: Source) -> Any:
    result = None
    last_error = ""
    try:
        result = await crawler.arun(url=url, config=make_run_config(source))
        html = result_html(result) if result is not None else ""
        if getattr(result, "success", False) and not html_looks_empty(html):
            return result
        last_error = getattr(result, "error_message", "") or "empty html"
    except Exception as exc:  # noqa: BLE001
        last_error = str(exc)
        result = None

    should_retry = any(token in last_error for token in ("TIMED_OUT", "Timeout", "ERR_CONNECTION"))
    if should_retry:
        logger.info("超时/断连，重试一次: %s", url)
        await asyncio.sleep(2)
        try:
            result = await crawler.arun(url=url, config=make_run_config(source))
            html = result_html(result) if result is not None else ""
            if getattr(result, "success", False) and not html_looks_empty(html):
                return result
            last_error = getattr(result, "error_message", "") or last_error
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)

    logger.info("浏览器结果不可用，改用 HTTP 回退: %s", url)
    try:
        fallback_html = await fetch_html_http(url)
        if not html_looks_empty(fallback_html):
            return FallbackResult(url=url, html=fallback_html)
    except Exception as exc:  # noqa: BLE001
        last_error = f"{last_error}; HTTP回退: {exc}".strip("; ")
        logger.warning("HTTP 回退失败 %s: %s", url, exc)
    if result is not None:
        return result
    return FallbackResult(url=url, html="", success=False, error_message=last_error)


async def fetch_rss_articles(source: Source) -> tuple[list[Article], list[str]]:
    articles: list[Article] = []
    errors: list[str] = []
    for rss_url in source.rss_urls:
        logger.info("RSS 采集 %s | %s", source.name_zh, rss_url)
        try:
            xml_text = await fetch_html_http(rss_url)
            articles.extend(extract_rss(xml_text, source, rss_url))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{rss_url}: {exc}")
            logger.warning("RSS 失败 %s: %s", rss_url, exc)
    return articles, errors


async def collect_source(
    crawler: AsyncWebCrawler,
    source: Source,
    days: int,
    semaphore: asyncio.Semaphore,
) -> tuple[list[Article], SourceStatus]:
    raw_articles: list[Article] = []
    errors: list[str] = []
    login_hit = False
    used_rss = False

    if source.prefer_rss and source.rss_urls:
        async with semaphore:
            rss_articles, rss_errors = await fetch_rss_articles(source)
        raw_articles.extend(rss_articles)
        errors.extend(rss_errors)
        used_rss = True

    if len(raw_articles) < 5:
        for url in source.urls:
            async with semaphore:
                logger.info("采集 %s | %s", source.name_zh, url)
                try:
                    result = await crawl_page(crawler, url, source)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{url}: {exc}")
                    logger.warning("采集异常 %s: %s", url, exc)
                    continue

            if not getattr(result, "success", False):
                errors.append(f"{url}: {getattr(result, 'error_message', 'unknown error')}")
                continue

            html = result_html(result)
            markdown = result_markdown(result)
            page_articles = merge_articles(
                [
                    extract_by_selectors(html, source, url),
                    extract_json_ld(html, source, url),
                    extract_markdown_links(markdown, source, url),
                ],
                source.max_items,
            )
            if looks_like_login(html, len(page_articles)):
                login_hit = True
            raw_articles.extend(page_articles)

    if source.rss_urls and not used_rss and len(raw_articles) < 5:
        async with semaphore:
            rss_articles, rss_errors = await fetch_rss_articles(source)
        raw_articles.extend(rss_articles)
        errors.extend(rss_errors)
        used_rss = bool(rss_articles)

    kept = [a for a in merge_articles([raw_articles], source.max_items) if article_should_keep(a, source, days)]
    note = ""
    if login_hit or (source.paywall_likely and not kept and not raw_articles):
        note = "可能需要登录/订阅，公开页未解析到条目"
    elif used_rss and raw_articles:
        note = "已用 RSS/Google News 通道"
    elif source.paywall_likely:
        note = "可能仅有标题，正文或深度内容需订阅"
    elif raw_articles and errors:
        note = "部分页面受反爬限制，已用备用通道补齐"

    status = SourceStatus(
        key=source.key,
        name_zh=source.name_zh,
        ok=not errors or bool(raw_articles),
        count=len(raw_articles),
        kept=len(kept),
        error="; ".join(errors[:2])[:240],
        note=note,
    )
    return kept, status


def extract_article_text(html: str) -> str:
    soup = soup_of(html)
    for selector in ("article", ".entry-content", ".post-content", ".article-body", "main", ".usa-prose"):
        node = soup.select_one(selector)
        if node:
            text = clean_text(node.get_text(" ", strip=True), 2500)
            if len(text) > 120:
                return text
    return clean_text(soup.get_text(" ", strip=True), 2500)


async def enrich_bodies(crawler: AsyncWebCrawler, articles: list[Article], limit: int) -> None:
    targets = articles if not limit else articles[:limit]
    if not targets:
        return
    semaphore = asyncio.Semaphore(2)
    run_cfg = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        page_timeout=45_000,
        delay_before_return_html=1.0,
        remove_overlay_elements=True,
        excluded_tags=["nav", "footer", "aside", "form"],
    )

    async def one(article: Article) -> None:
        html = ""
        async with semaphore:
            try:
                html = await fetch_html_http(article.url)
            except Exception:
                html = ""
            if html_looks_empty(html):
                try:
                    result = await crawler.arun(url=article.url, config=run_cfg)
                    html = result_html(result) if getattr(result, "success", False) else html
                except Exception as exc:  # noqa: BLE001
                    logger.debug("正文抓取失败 %s: %s", article.url, exc)
                    return
        text = extract_article_text(html)
        if len(text) > 80:
            article.body = text
            if (not article.excerpt) or "<" in article.excerpt or len(article.excerpt) < 40:
                article.excerpt = clean_text(text, 320)

    await asyncio.gather(*(one(a) for a in targets))


def dedupe(articles: list[Article]) -> list[Article]:
    by_url: dict[str, Article] = {}
    for article in articles:
        key = article.url.rstrip("/").lower()
        if key not in by_url:
            by_url[key] = article
            continue
        existing = by_url[key]
        if len(article.excerpt) > len(existing.excerpt):
            existing.excerpt = article.excerpt
        if article.date and not existing.date:
            existing.date = article.date

    unique = list(by_url.values())
    title_seen: dict[str, Article] = {}
    result: list[Article] = []
    for article in unique:
        title_key = re.sub(r"\W+", " ", article.title.lower()).strip()
        if title_key in title_seen:
            continue
        title_seen[title_key] = article
        result.append(article)
    return result


def split_sections(articles: list[Article]) -> dict[str, list[Article]]:
    sections = {
        "cross": [],
        "official": [],
        "oil": [],
        "sanctions_news": [],
    }
    for article in articles:
        themes = set(article.themes)
        if "oil_transport" in themes and "sanctions" in themes:
            sections["cross"].append(article)
        elif article.group == "official":
            sections["official"].append(article)
        elif "oil_transport" in themes:
            sections["oil"].append(article)
        else:
            sections["sanctions_news"].append(article)
    for key in sections:
        sections[key].sort(key=lambda a: (a.date or "0000-00-00", a.title), reverse=True)
    return sections


def render_item(article: Article) -> str:
    date_part = f"**{article.date}** · " if article.date else ""
    theme_map = {"oil_transport": "石油运输", "sanctions": "经济制裁"}
    theme_part = "、".join(theme_map.get(t, t) for t in article.themes)
    lines = [
        f"- {date_part}[{article.title}]({article.url})",
        f"  - 来源：{article.source}" + (f" · 主题：{theme_part}" if theme_part else ""),
    ]
    if article.excerpt:
        lines.append(f"  - 摘要：{article.excerpt}")
    return "\n".join(lines)


def build_brief(
    articles: list[Article],
    statuses: list[SourceStatus],
    days: int,
) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    sections = split_sections(articles)
    lines = [
        f"# 油轮行业每日简报（{today}）",
        "",
        f"筛选窗口：近 {days} 天；主题：油轮基本面、运力、租家/同行、石油贸易、炼厂、地缘与政策。",
        "",
        f"- 入选条目：**{len(articles)}**",
        f"- 交叉议题（石油运输 × 制裁）：**{len(sections['cross'])}**",
        f"- 官方制裁：**{len(sections['official'])}**",
        f"- 石油运输：**{len(sections['oil'])}**",
        f"- 其他制裁相关航运新闻：**{len(sections['sanctions_news'])}**",
        "",
        "## 一、交叉议题（石油运输 × 制裁）",
        "",
    ]
    if sections["cross"]:
        lines.extend(render_item(a) for a in sections["cross"])
    else:
        lines.append("本日未见同时命中两个主题的条目。")

    lines += ["", "## 二、官方制裁动态", ""]
    if sections["official"]:
        by_source: dict[str, list[Article]] = defaultdict(list)
        for article in sections["official"]:
            by_source[article.source].append(article)
        for source_name, items in by_source.items():
            lines.append(f"### {source_name}")
            lines.append("")
            lines.extend(render_item(a) for a in items)
            lines.append("")
    else:
        lines.append("本日官方源暂无符合筛选条件的更新。")
        lines.append("")

    lines += ["## 三、石油运输与油轮市场", ""]
    if sections["oil"]:
        lines.extend(render_item(a) for a in sections["oil"])
    else:
        lines.append("本日暂无单独归入石油运输的条目。")

    lines += ["", "## 四、其他制裁相关航运新闻", ""]
    if sections["sanctions_news"]:
        lines.extend(render_item(a) for a in sections["sanctions_news"])
    else:
        lines.append("本日暂无此类条目。")

    lines += ["", "## 五、采集状态", "", "| 来源 | 解析条数 | 入选 | 状态 | 备注 |", "| --- | ---: | ---: | --- | --- |"]
    for status in statuses:
        state = "成功" if status.ok else "失败"
        note = status.note or status.error or "—"
        note = note.replace("|", "/")
        lines.append(f"| {status.name_zh} | {status.count} | {status.kept} | {state} | {note} |")

    lines += [
        "",
        "## 说明",
        "",
        "- Platts、TradeWinds 等付费墙站点优先走 RSS / Google News，通常只有标题，深度正文需订阅。",
        "- 同一新闻跨日期去重、结构化摘要与知识库检索由 intel.py 完成。",
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"


def save_outputs(articles: list[Article], statuses: list[SourceStatus], markdown: str) -> tuple[Path, Path]:
    import brief_store

    brief_store.migrate_legacy()
    stamp = datetime.now().strftime("%Y-%m-%d")
    brief_store.web_dir(stamp).mkdir(parents=True, exist_ok=True)
    md_path = brief_store.web_md(stamp)
    json_path = brief_store.web_json(stamp)
    md_path.write_text(markdown, encoding="utf-8")
    payload = {
        "date": stamp,
        "count": len(articles),
        "articles": [asdict(a) for a in articles],
        "sources": [asdict(s) for s in statuses],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        import report as report_builder

        report_builder.write_from_payload(payload, markdown)
    except Exception:
        logger.exception("对照页 HTML 生成失败（Markdown/JSON 已保存）")
        try:
            brief_store.publish_day(stamp)
        except Exception:
            logger.exception("合页生成失败")
    return md_path, json_path


async def run_once(days: int, deep: bool, only: str = "", catalog: bool = False, from_json: str = "", wechat: bool = False) -> None:
    load_env()
    setup_logging()
    selected = [s for s in SOURCES if s.enabled]
    if only:
        wanted = {key.strip() for key in only.split(",") if key.strip()}
        selected = [s for s in SOURCES if s.key in wanted]
        missing = wanted - {s.key for s in selected}
        if missing:
            raise SystemExit(f"未知源: {', '.join(sorted(missing))}")
    if from_json:
        logger.info("从 JSON 重写简报: %s", from_json)
        payload = json.loads(Path(from_json).read_text(encoding="utf-8"))
        article_fields = set(Article.__dataclass_fields__)
        status_fields = set(SourceStatus.__dataclass_fields__)
        all_articles = [
            Article(**{k: v for k, v in item.items() if k in article_fields})
            for item in payload.get("articles", [])
        ]
        statuses = [
            SourceStatus(**{k: v for k, v in item.items() if k in status_fields})
            for item in payload.get("sources", [])
        ]
        if deep and all_articles:
            try:
                browser_cfg = BrowserConfig(headless=True, verbose=False, enable_stealth=True, user_agent=USER_AGENT)
            except TypeError:
                browser_cfg = BrowserConfig(headless=True, verbose=False, user_agent=USER_AGENT)
            async with AsyncWebCrawler(config=browser_cfg) as crawler:
                selected_stories = cluster_and_rank(all_articles, config.DIGEST_STORY_LIMIT)
                await enrich_bodies(crawler, selected_stories, limit=config.DIGEST_STORY_LIMIT)
        markdown = build_oil_digest(all_articles)
        if catalog:
            markdown = markdown.rstrip() + "\n\n----\n\n" + build_brief(all_articles, statuses, days)
        md_path, json_path = save_outputs(all_articles, statuses, markdown)
        logger.info("简报已写入 %s", md_path)
        print("\n" + markdown)
        return
    logger.info("开始采集，回看 %s 天，深度抓取=%s，源数=%s", days, deep, len(selected))
    try:
        browser_cfg = BrowserConfig(
            headless=True,
            verbose=False,
            enable_stealth=True,
            user_agent=USER_AGENT,
        )
    except TypeError:
        browser_cfg = BrowserConfig(headless=True, verbose=False, user_agent=USER_AGENT)
    semaphore = asyncio.Semaphore(config.CONCURRENCY)
    all_articles: list[Article] = []
    statuses: list[SourceStatus] = []

    async with AsyncWebCrawler(config=browser_cfg) as crawler:
        tasks = [collect_source(crawler, source, days, semaphore) for source in selected]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for source, result in zip(selected, results, strict=True):
            if isinstance(result, Exception):
                logger.error("源失败 %s: %s", source.name_zh, result)
                statuses.append(
                    SourceStatus(
                        key=source.key,
                        name_zh=source.name_zh,
                        ok=False,
                        error=str(result),
                    )
                )
                continue
            articles, status = result
            all_articles.extend(articles)
            statuses.append(status)
            logger.info("%s: 解析 %s 条，入选 %s 条", status.name_zh, status.count, status.kept)

        all_articles = dedupe(all_articles)
        if deep and all_articles:
            selected_stories = cluster_and_rank(all_articles, config.DIGEST_STORY_LIMIT)
            logger.info("抓取正文，准备撰写简报，共 %s 条", len(selected_stories))
            await enrich_bodies(crawler, selected_stories, limit=config.DIGEST_STORY_LIMIT)

    if wechat:
        from wechat import collect as collect_wechat

        wx_articles, wx_statuses, wx_note = await collect_wechat(days=None, topic_filter=True)
        logger.info("微信公众号: %s，入选 %s 条", wx_note, len(wx_articles))
        for item in wx_articles:
            all_articles.append(
                Article(
                    title=item.title,
                    url=item.url,
                    source=item.source,
                    source_key=item.source_key,
                    group=item.group,
                    date=item.date,
                    published_at=getattr(item, "published_at", None),
                    excerpt=item.excerpt,
                    body=item.body,
                    themes=item.themes,
                    matched_keywords=item.matched_keywords,
                    page_url=item.page_url,
                    language="zh",
                )
            )
        for item in wx_statuses:
            statuses.append(
                SourceStatus(
                    key=item.key,
                    name_zh=item.name_zh,
                    ok=item.ok,
                    count=item.count,
                    kept=item.kept,
                    error=item.error,
                    note=item.note,
                )
            )
        all_articles = dedupe(all_articles)

    markdown = build_oil_digest(all_articles)
    if catalog:
        markdown = markdown.rstrip() + "\n\n----\n\n" + build_brief(all_articles, statuses, days)
    md_path, json_path = save_outputs(all_articles, statuses, markdown)
    logger.info("简报已写入 %s", md_path)
    logger.info("JSON 已写入 %s", json_path)
    try:
        from intel import process_day

        intel_result = process_day(datetime.now().strftime("%Y-%m-%d"))
        logger.info("知识库: 本日新增 %s / 全库 %s", intel_result.get("fresh"), intel_result.get("library"))
    except Exception:
        logger.exception("情报入库失败（网页采集结果已保存）")
    print("\n" + markdown)


def run_schedule(days: int, deep: bool, at: str, wechat: bool = False) -> None:
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("未安装 apscheduler，请执行 pip install -r requirements.txt") from exc

    hour, minute = (int(part) for part in at.split(":", 1))
    scheduler = BlockingScheduler()

    def job() -> None:
        asyncio.run(run_once(days=days, deep=deep, wechat=wechat))

    scheduler.add_job(job, CronTrigger(hour=hour, minute=minute))
    print(f"已启动每日 {at} 自动采集。按 Ctrl+C 结束。")
    print("也可使用 Windows 任务计划程序每天执行: python crawl.py")
    job()
    scheduler.start()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="油轮行业网页新闻采集")
    parser.add_argument("--days", type=int, default=config.LOOKBACK_DAYS, help="回看天数，默认 3")
    parser.add_argument("--deep", action="store_true", default=True, help="抓取正文后撰写简报（默认开启）")
    parser.add_argument("--no-deep", action="store_true", help="只根据标题生成简报，不抓正文")
    parser.add_argument("--catalog", action="store_true", help="在简报后附加原始条目目录")
    parser.add_argument("--from-json", default="", help="用已有 JSON 重写简报，如 2026-08-25 或 briefs/日期/web/articles.json")
    parser.add_argument("--schedule", action="store_true", help="按天定时运行")
    parser.add_argument("--at", default=config.DAILY_RUN_AT, help="定时时间，默认 12:10")
    parser.add_argument(
        "--only",
        default="",
        help="只采集指定源，逗号分隔，如 ofac,gcaptain,ofsi",
    )
    parser.add_argument(
        "--wechat",
        action="store_true",
        help="合并本地 WeWe RSS 中的微信公众号正文（先运行 python wechat.py 检测服务）",
    )
    return parser.parse_args()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    deep = not args.no_deep
    if args.schedule:
        run_schedule(days=args.days, deep=deep, at=args.at, wechat=args.wechat)
        return
    from_json = args.from_json
    if from_json:
        import brief_store

        from_json = str(brief_store.resolve_web_json(from_json))
    asyncio.run(
        run_once(
            days=args.days,
            deep=deep,
            only=args.only,
            catalog=args.catalog,
            from_json=from_json,
            wechat=args.wechat,
        )
    )


if __name__ == "__main__":
    main()
