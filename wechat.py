"""微信公众号正文采集（与 crawl.py 分开）。

公众号没有稳定公开目录，正文走本地 WeWe RSS（微信读书扫码后出 feed）。
本文件只负责：探测服务、拉 RSS/JSON、抽出正文、按账号与日期筛选。
不把扫码登录写进航运网页采集器。

用法:
  python wechat.py                 # 先自动更新 WeWe RSS，再拉取当日稿
  python wechat.py --probe         # 只检测 WeWe RSS 是否在跑
  python wechat.py --refresh-only  # 只触发更新
  python wechat.py --skip-refresh  # 不更新，只读缓存
  python crawl.py --wechat


先启动 WeWe RSS（需已安装 Docker）:
  docker compose -f docker-compose.wechat.yml up -d
然后浏览器打开 http://127.0.0.1:4000
  账号管理 → 扫码登录微信读书（不要勾选 24 小时退出）
  公众号源 → 用分享链接添加，一天只加几个，避免小黑屋
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from html import unescape
from typing import Any
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup
from dateutil import parser as date_parser

import config
from digest import load_env
from wechat_accounts import ACCOUNTS, NAME_ALIASES, WechatAccount, by_name

logger = logging.getLogger("wechat")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# 并入航运制裁简报时用；单独跑 wechat.py 默认不过滤，以免漏掉当天推送
TOPIC_KEYWORDS = [
    "油轮",
    "航运",
    "船舶",
    "霍尔木兹",
    "影子船队",
    "vlcc",
    "suezmax",
    "aframax",
    "原油",
    "成品油",
    "运价",
    "租船",
    "船东",
    "炼厂",
    "新造船",
    "拆解",
    "制裁",
    "克拉克森",
    "tanker",
    "freight",
    "crude",
    "charter",
    "ofac",
]


@dataclass
class WechatArticle:
    title: str
    url: str
    source: str
    source_key: str
    group: str = "wechat"
    date: str | None = None
    published_at: str | None = None
    excerpt: str = ""
    body: str = ""
    themes: list[str] = field(default_factory=list)
    matched_keywords: list[str] = field(default_factory=list)
    page_url: str = ""


@dataclass
class WechatStatus:
    key: str
    name_zh: str
    ok: bool
    count: int = 0
    kept: int = 0
    error: str = ""
    note: str = ""


def rss_base() -> str:
    load_env()
    return (os.getenv("WECHAT_RSS_BASE") or "http://127.0.0.1:4000").rstrip("/")


def wewe_auth_code() -> str:
    load_env()
    return (os.getenv("WECHAT_AUTH_CODE") or "123567").strip()


def wewe_update_delay_sec() -> float:
    load_env()
    try:
        return max(5.0, float(os.getenv("WECHAT_UPDATE_DELAY_SEC") or "20"))
    except ValueError:
        return 20.0


def html_to_text(raw: str) -> str:
    if not raw:
        return ""
    text = BeautifulSoup(unescape(raw), "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 10_000_000_000:
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts)
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = date_parser.parse(text)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
        return parsed
    except (ValueError, OverflowError, TypeError):
        return None


def parse_date(value: Any) -> str | None:
    parsed = parse_datetime(value)
    return parsed.strftime("%Y-%m-%d") if parsed else None


def news_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    """前一天中午 12:00 至当前时刻，纳入当日已发布的稿。"""
    now = now or datetime.now()
    today_noon = now.replace(hour=12, minute=0, second=0, microsecond=0)
    start = today_noon - timedelta(days=1)
    return start, now


def in_news_window(published: datetime | None, start: datetime, end: datetime) -> bool:
    if published is None:
        return False
    return start <= published <= end


def resolve_window(days: int | None = None, now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or datetime.now()
    if days is None:
        return news_window(now)
    if days <= 0:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, now
    return now - timedelta(days=days), now


def window_label(start: datetime, end: datetime) -> str:
    return f"{start.strftime('%Y-%m-%d %H:%M')} 至 {end.strftime('%Y-%m-%d %H:%M')}"


def flatten_author(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("mpName") or value.get("author") or "").strip()
    if isinstance(value, list) and value:
        return flatten_author(value[0])
    return str(value or "").strip()


def match_account(mp_name: str) -> WechatAccount | None:
    name = (mp_name or "").strip()
    mapping = by_name()
    if name in mapping:
        return mapping[name]
    alias = NAME_ALIASES.get(name.lower())
    if alias and alias in mapping:
        return mapping[alias]
    lowered = name.lower()
    for alias, canonical in NAME_ALIASES.items():
        alias_l = alias.lower()
        if lowered == alias_l or alias_l in lowered:
            found = mapping.get(canonical)
            if found:
                return found
    for account in ACCOUNTS:
        if account.name in name or name in account.name:
            return account
    return None


def pick_topic_hits(text: str) -> list[str]:
    blob = (text or "").lower()
    hits = []
    for word in TOPIC_KEYWORDS:
        if word.lower() in blob:
            hits.append(word)
    return hits


def item_field(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in item and item[key] not in (None, ""):
            return item[key]
    return ""


def article_from_item(item: dict[str, Any], page_url: str) -> WechatArticle | None:
    title = html_to_text(str(item_field(item, "title", "name") or ""))[:240]
    url = str(item_field(item, "link", "url", "guid") or "").strip()
    if url and not url.startswith("http"):
        url = urljoin(page_url, url)
    mp_name = flatten_author(item_field(item, "mpName", "mp_name", "author", "source"))
    account = match_account(mp_name) or match_account(title)
    if not title or not url:
        return None
    if not account:
        if mp_name:
            return None
        account = WechatAccount("unknown", "微信公众号")
    html_body = str(
        item_field(item, "content:encoded", "content", "content_html", "html", "description", "summary") or ""
    )
    body = html_to_text(html_body)
    excerpt = body[:280]
    published = parse_datetime(
        item_field(
            item,
            "date_modified",
            "date_published",
            "datePublished",
            "pubDate",
            "published",
            "publishTime",
            "publish_time",
            "isoDate",
            "date",
            "updated",
        )
    )
    date = published.strftime("%Y-%m-%d") if published else None
    published_at = published.strftime("%Y-%m-%d %H:%M:%S") if published else None
    hits = pick_topic_hits(" ".join([title, body, account.name]))
    themes = []
    if any(w in hits for w in ("油轮", "航运", "船舶", "霍尔木兹", "vlcc", "原油", "运价", "租船", "tanker", "freight", "crude")):
        themes.append("oil_transport")
    if any(w in hits for w in ("制裁", "ofac", "霍尔木兹")):
        themes.append("sanctions")
    if not themes and account.shipping:
        themes.append("oil_transport")
    return WechatArticle(
        title=title,
        url=url,
        source=account.name,
        source_key=f"wechat_{account.key}",
        date=date,
        published_at=published_at,
        excerpt=excerpt,
        body=body,
        themes=themes,
        matched_keywords=hits[:8],
        page_url=page_url,
    )


def parse_json_feed(payload: Any, page_url: str) -> list[WechatArticle]:
    items: list[Any]
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("items") or payload.get("articles") or payload.get("data") or []
        if isinstance(items, dict):
            items = items.get("list") or items.get("items") or []
    else:
        items = []
    articles = []
    for item in items:
        if not isinstance(item, dict):
            continue
        flat = dict(item)
        mp = item.get("mp") or item.get("mpInfo")
        if isinstance(mp, dict):
            flat.setdefault("mpName", mp.get("name") or mp.get("mpName") or "")
        author = item.get("author")
        if isinstance(author, dict):
            flat["author"] = author.get("name") or author.get("mpName") or ""
            flat.setdefault("mpName", flat["author"])
        article = article_from_item(flat, page_url)
        if article:
            articles.append(article)
    return articles


def parse_rss_feed(xml_text: str, page_url: str) -> list[WechatArticle]:
    soup = BeautifulSoup(xml_text, "xml")
    nodes = soup.find_all("item") or soup.find_all("entry")
    articles = []
    for node in nodes:
        item = {
            "title": node.find("title").get_text(" ", strip=True) if node.find("title") else "",
            "link": "",
            "description": "",
            "content": "",
            "pubDate": "",
            "mpName": "",
        }
        link = node.find("link")
        if link is not None:
            item["link"] = (link.get_text(" ", strip=True) or link.get("href") or "").strip()
        if not item["link"] and node.find("guid") is not None:
            item["link"] = node.find("guid").get_text(" ", strip=True)
        encoded = node.find("content:encoded") or node.find("encoded") or node.find("content")
        desc = node.find("description") or node.find("summary")
        item["content"] = encoded.get_text() if encoded is not None else ""
        item["description"] = desc.get_text() if desc is not None else ""
        for tag in ("pubDate", "published", "updated", "dc:date"):
            found = node.find(tag)
            if found is not None:
                item["pubDate"] = found.get_text(" ", strip=True)
                break
        author = node.find("author") or node.find("dc:creator") or node.find("source")
        if author is not None:
            item["mpName"] = author.get_text(" ", strip=True)
        article = article_from_item(item, page_url)
        if article:
            articles.append(article)
    return articles


async def http_get(url: str, headers: dict[str, str] | None = None, timeout: float = 180.0) -> tuple[int, str, str]:
    import httpx

    merged = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, application/rss+xml, text/xml, */*",
    }
    if headers:
        merged.update(headers)
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=timeout,
        headers=merged,
        trust_env=False,
    ) as client:
        response = await client.get(url)
        content_type = response.headers.get("content-type", "")
        return response.status_code, content_type, response.text


async def http_post_json(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None, timeout: float = 180.0) -> tuple[int, str]:
    import httpx

    merged = {"User-Agent": USER_AGENT, "Content-Type": "application/json", "Accept": "application/json"}
    if headers:
        merged.update(headers)
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout, headers=merged, trust_env=False) as client:
        response = await client.post(url, content=json.dumps(payload))
        return response.status_code, response.text


async def probe_service() -> tuple[bool, str]:
    base = rss_base()
    try:
        status, _, text = await http_get(base + "/")
    except Exception as exc:  # noqa: BLE001
        return False, f"无法连接 {base}（{exc}）"
    if status >= 500:
        return False, f"{base} 返回 {status}"
    return True, f"WeWe RSS 可访问 {base}（HTTP {status}，{len(text)} 字节）"


def setup_hint() -> str:
    return f"""
微信正文通道未就绪。公众号不走 crawl.py 的网页爬虫，需要先起 WeWe RSS：

  1. 安装 Docker Desktop 并启动
  2. 在项目目录执行:
       docker compose -f docker-compose.wechat.yml up -d
  3. 浏览器打开 {rss_base()}
  4. 账号管理 → 扫码登录微信读书（不要勾选 24 小时后自动退出）
  5. 公众号源 → 用分享链接添加名单中的号（一天只加几个）
  6. 再运行: python wechat.py

.env 里可设 WECHAT_RSS_BASE={rss_base()}
采集前会自动请求 WeWe RSS 更新公众号，不必再手动点「更新」。
""".strip()


def _trpc_data(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    result = payload.get("result")
    if isinstance(result, dict) and "data" in result:
        data = result["data"]
        if isinstance(data, dict) and "json" in data:
            return data["json"]
        return data
    return payload


async def list_wewe_feeds() -> list[dict[str, Any]]:
    base = rss_base()
    auth = {"Authorization": wewe_auth_code()}
    payload = json.dumps({"json": {"limit": 1000}}, separators=(",", ":"))
    url = f"{base}/trpc/feed.list?input={quote(payload)}"
    status, _, text = await http_get(url, headers=auth, timeout=40.0)
    if status != 200 or not text.strip():
        raise RuntimeError(f"列出公众号失败 HTTP {status}")
    data = _trpc_data(json.loads(text))
    items = []
    if isinstance(data, dict):
        items = data.get("items") or []
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict) and item.get("id")]


def feeds_to_refresh(feeds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matched = []
    for item in feeds:
        name = str(item.get("mpName") or item.get("name") or "")
        if match_account(name):
            matched.append(item)
    return matched or feeds


async def trigger_feed_update(feed_id: str) -> str:
    base = rss_base()
    url = f"{base}/feeds/{feed_id}.json?update=true&limit=1"
    status, _, text = await http_get(url, timeout=120.0)
    if status == 200:
        return "ok"
    return f"HTTP {status} {(text or '')[:80]}"


async def trigger_refresh_all_via_trpc() -> str:
    base = rss_base()
    status, text = await http_post_json(
        f"{base}/trpc/feed.refreshArticles",
        {"json": {}},
        headers={"Authorization": wewe_auth_code()},
        timeout=900.0,
    )
    if status in {200, 204}:
        return "ok"
    return f"HTTP {status} {(text or '')[:120]}"


async def refresh_wewe_feeds() -> str:
    """采集前主动拉新，等价于后台点「更新」而不依赖容器 cron。"""
    try:
        feeds = await list_wewe_feeds()
    except Exception as exc:  # noqa: BLE001
        logger.warning("无法列出公众号，改走更新全部: %s", exc)
        result = await trigger_refresh_all_via_trpc()
        return f"更新全部：{result}"

    targets = feeds_to_refresh(feeds)
    if not targets:
        return "WeWe RSS 里还没有公众号源"
    delay = wewe_update_delay_sec()
    logger.info("开始更新 %s 个公众号（间隔 %.0fs）", len(targets), delay)
    ok = 0
    errors: list[str] = []
    for i, item in enumerate(targets):
        feed_id = str(item.get("id") or "")
        name = str(item.get("mpName") or feed_id)
        logger.info("更新 %s (%s)", name, feed_id)
        try:
            result = await trigger_feed_update(feed_id)
        except Exception as exc:  # noqa: BLE001
            result = str(exc)
        if result == "ok":
            ok += 1
        else:
            errors.append(f"{name}: {result}")
            logger.warning("更新失败 %s: %s", name, result)
        if i < len(targets) - 1:
            await asyncio.sleep(delay)
    note = f"已更新 {ok}/{len(targets)} 个公众号"
    if errors:
        note += "；失败 " + "；".join(errors[:3])
    return note


async def fetch_all_feeds() -> tuple[list[WechatArticle], str]:
    base = rss_base()
    urls = [
        f"{base}/feeds/all.json?limit=120",
        f"{base}/feeds/all.json?limit=80",
        f"{base}/feeds/all.json?limit=50",
        f"{base}/feeds/all.json",
        f"{base}/feeds/all.rss",
    ]
    last_error = ""
    for url in urls:
        try:
            status, content_type, text = await http_get(url)
        except Exception as exc:  # noqa: BLE001
            last_error = f"{url}: {exc}"
            continue
        if status != 200 or not text.strip():
            last_error = f"{url}: HTTP {status}"
            continue
        ctype = content_type.lower()
        try:
            if "json" in ctype or text.lstrip().startswith("{") or text.lstrip().startswith("["):
                articles = parse_json_feed(json.loads(text), url)
            else:
                articles = parse_rss_feed(text, url)
        except Exception as exc:  # noqa: BLE001
            last_error = f"{url}: 解析失败 {exc}"
            continue
        if articles:
            return articles, f"已用 {url}"
        last_error = f"{url}: 有响应但没有匹配到名单中的公众号"
    return [], last_error or "未拉到 feed"


def filter_articles(
    articles: list[WechatArticle],
    start: datetime,
    end: datetime,
    topic_filter: bool = False,
) -> list[WechatArticle]:
    kept: list[WechatArticle] = []
    for article in articles:
        published = parse_datetime(article.published_at) or parse_datetime(article.date)
        if not in_news_window(published, start, end):
            continue
        account = next((a for a in ACCOUNTS if a.name == article.source), None)
        shipping = bool(account and account.shipping)
        if topic_filter and not shipping and not article.matched_keywords:
            continue
        if (not topic_filter) and account and (not account.shipping) and not article.matched_keywords:
            continue
        kept.append(article)
    kept.sort(key=lambda a: a.published_at or a.date or "", reverse=True)
    return kept


def statuses_from(articles: list[WechatArticle], raw_count: int, note: str, error: str = "") -> list[WechatStatus]:
    by_key: dict[str, WechatStatus] = {}
    for account in ACCOUNTS:
        by_key[account.key] = WechatStatus(
            key=f"wechat_{account.key}",
            name_zh=account.name,
            ok=not error,
            note=note,
            error=error,
        )
    for article in articles:
        key = article.source_key.replace("wechat_", "", 1)
        status = by_key.get(key)
        if status is None:
            status = WechatStatus(key=article.source_key, name_zh=article.source, ok=True, note=note)
            by_key[key] = status
        status.kept += 1
        status.count += 1
    if not articles and raw_count:
        # 有 feed 但未匹配账号
        pass
    return list(by_key.values())


async def collect(
    days: int | None = None,
    topic_filter: bool = False,
    refresh: bool = True,
) -> tuple[list[WechatArticle], list[WechatStatus], str]:
    ok, probe_msg = await probe_service()
    if not ok:
        statuses = [
            WechatStatus(
                key="wechat",
                name_zh="微信公众号",
                ok=False,
                error=probe_msg,
                note="服务未启动",
            )
        ]
        return [], statuses, probe_msg
    refresh_note = ""
    if refresh:
        try:
            refresh_note = await refresh_wewe_feeds()
            logger.info("%s", refresh_note)
        except Exception as exc:  # noqa: BLE001
            refresh_note = f"主动更新失败（将读取现有缓存）: {exc}"
            logger.warning("%s", refresh_note)
    raw, note = await fetch_all_feeds()
    start, end = resolve_window(days)
    kept = filter_articles(raw, start, end, topic_filter)
    bits = [note, f"窗口 {window_label(start, end)}"]
    if refresh_note:
        bits.insert(0, refresh_note)
    full_note = "；".join(bits)
    statuses = statuses_from(kept, len(raw), full_note)
    if not raw:
        statuses = [
            WechatStatus(
                key="wechat",
                name_zh="微信公众号",
                ok=False,
                count=0,
                kept=0,
                error=note,
                note="已连上 WeWe RSS，但还没有名单中的文章。请确认已扫码并添加公众号。",
            )
        ]
        return kept, statuses, note
    return kept, statuses, full_note


def save_snapshot(
    articles: list[WechatArticle],
    statuses: list[WechatStatus],
    window: tuple[datetime, datetime] | None = None,
) -> Any:
    import brief_store

    brief_store.migrate_legacy()
    stamp = datetime.now().strftime("%Y-%m-%d")
    start, end = window or resolve_window()
    path = brief_store.wechat_json(stamp)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "date": stamp,
        "count": len(articles),
        "window": {
            "start": start.strftime("%Y-%m-%d %H:%M:%S"),
            "end": end.strftime("%Y-%m-%d %H:%M:%S"),
            "label": window_label(start, end),
        },
        "articles": [asdict(a) for a in articles],
        "sources": [asdict(s) for s in statuses],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        brief_store.publish_day(stamp)
    except Exception:
        logger.exception("合页生成失败")
    return path


def print_articles(articles: list[WechatArticle]) -> None:
    if not articles:
        print("没有入选条目。")
        return
    for article in articles:
        flag = f"正文 {len(article.body)} 字" if len(article.body) >= 280 else "正文偏短/仅摘要"
        print(f"- [{article.source}] {article.title}")
        print(f"  {flag}  {article.published_at or article.date or '无日期'}  {article.url}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从本地 WeWe RSS 拉取微信公众号正文")
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="覆盖默认时间窗，按最近 N 天筛选。默认：前一天 12:00 至当前时刻",
    )
    parser.add_argument("--probe", action="store_true", help="只检测 WeWe RSS 是否可访问")
    parser.add_argument("--filter", action="store_true", help="按制裁/航运关键词过滤后再输出")
    parser.add_argument("--skip-refresh", action="store_true", help="不触发 WeWe RSS 更新，只读已缓存文章")
    parser.add_argument("--refresh-only", action="store_true", help="只更新 WeWe RSS，不写简报")
    return parser.parse_args()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    args = parse_args()
    load_env()

    import asyncio

    async def _run() -> int:
        if args.probe:
            ok, msg = await probe_service()
            print(msg)
            if not ok:
                print()
                print(setup_hint())
                return 2
            return 0
        if args.refresh_only:
            ok, msg = await probe_service()
            print(msg)
            if not ok:
                print()
                print(setup_hint())
                return 2
            note = await refresh_wewe_feeds()
            print(note)
            return 0 if "失败" not in note or "已更新" in note else 1
        articles, statuses, note = await collect(
            args.days,
            topic_filter=args.filter,
            refresh=not args.skip_refresh,
        )
        window = resolve_window(args.days)
        failed = statuses and not statuses[0].ok and not articles
        if failed and "无法连接" in (statuses[0].error or ""):
            print(statuses[0].error)
            print()
            print(setup_hint())
            save_snapshot(articles, statuses, window=window)
            return 2
        path = save_snapshot(articles, statuses, window=window)
        print(note)
        print(f"入选 {len(articles)} 条，已写入 {path}")
        print_articles(articles)
        if articles:
            try:
                from wechat_digest import build_from_json

                md_path, html_path = build_from_json(path)
                print(f"公众号简报已写入 {md_path}")
                print(f"移动端页面已写入 {html_path}")
            except Exception as exc:  # noqa: BLE001
                logging.exception("公众号简报生成失败: %s", exc)
        try:
            from intel import process_day

            result = process_day(datetime.now().strftime("%Y-%m-%d"))
            print(f"知识库本日新增 {result.get('fresh')} 条，全库 {result.get('library')} 条")
        except Exception as exc:  # noqa: BLE001
            logging.exception("情报入库失败: %s", exc)
        if not articles:
            print()
            print("若服务已启动仍为空：在 WeWe RSS 里添加名单中的公众号后再跑一次。")
        return 0 if articles or not failed else 1

    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
