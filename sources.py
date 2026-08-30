"""信息源定义。选择器可按站点改版调整；解析失败时会回退到链接/Markdown 抽取。"""

from dataclasses import dataclass, field
from urllib.parse import quote_plus


def google_news_rss(query: str) -> str:
    """公开的 Google News RSS，用于首页被 Cloudflare/付费墙挡住的源。"""
    return (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
    )


def google_news_rss_zh(query: str) -> str:
    return (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(query)}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
    )


@dataclass
class Source:
    key: str
    name: str
    name_zh: str
    urls: list[str]
    group: str  # official / third_party
    item_selectors: list[str] = field(default_factory=list)
    title_selectors: list[str] = field(default_factory=list)
    link_selectors: list[str] = field(default_factory=list)
    date_selectors: list[str] = field(default_factory=list)
    excerpt_selectors: list[str] = field(default_factory=list)
    allowed_domains: list[str] = field(default_factory=list)
    extra_keywords: list[str] = field(default_factory=list)
    wait_for: str | None = None
    max_items: int = 40
    paywall_likely: bool = False
    focus_official: bool = False
    rss_urls: list[str] = field(default_factory=list)
    prefer_rss: bool = False
    enabled: bool = True
    keep_broad: bool = False  # 油轮专栏/机构报告页：放宽关键词过滤
    language: str = "en"


SOURCES: list[Source] = [
    Source(
        key="spglobal",
        name="Platts / S&P Global",
        name_zh="Platts（标普全球）",
        urls=["https://www.spglobal.com/energy/en/news-research/latest-news"],
        group="third_party",
        item_selectors=["article", ".search-result", ".news-item", "li"],
        title_selectors=["h2 a", "h3 a", "h2", "h3", "a"],
        link_selectors=["a[href*='news-research']", "h2 a", "h3 a", "a"],
        date_selectors=["time", ".date"],
        excerpt_selectors=["p"],
        allowed_domains=["spglobal.com", "news.google.com"],
        paywall_likely=True,
        prefer_rss=True,
        keep_broad=True,
        rss_urls=[
            google_news_rss(
                'site:spglobal.com (tanker OR VLCC OR crude OR "oil market" OR refinery OR freight OR Hormuz)'
            ),
        ],
    ),
    Source(
        key="iea",
        name="IEA",
        name_zh="IEA（国际能源署）",
        urls=[
            "https://www.iea.org/reports/oil-market-report",
            "https://www.iea.org/news",
            "https://www.iea.org/reports",
        ],
        group="official",
        item_selectors=["article", ".m-news-listing__item", ".o-card", "li", ".m-block-list__item"],
        title_selectors=["h2 a", "h3 a", "h2", "a"],
        link_selectors=["h2 a", "h3 a", "a"],
        date_selectors=["time", ".date"],
        excerpt_selectors=["p", ".excerpt"],
        allowed_domains=["iea.org", "news.google.com"],
        extra_keywords=["oil", "crude", "tanker", "inventory", "demand", "supply", "refinery", "stock"],
        keep_broad=True,
        prefer_rss=True,
        rss_urls=[
            google_news_rss('site:iea.org (oil OR crude OR "oil market" OR inventory OR tanker OR OPEC)'),
        ],
    ),
    Source(
        key="shippingwatch",
        name="ShippingWatch",
        name_zh="ShippingWatch",
        urls=["https://shippingwatch.com/carriers/Tanker"],
        group="third_party",
        item_selectors=["article", ".teaser", ".news-item", "li", ".card"],
        title_selectors=["h2 a", "h3 a", "h2", "a"],
        link_selectors=["h2 a", "h3 a", "a"],
        date_selectors=["time", ".date"],
        excerpt_selectors=["p"],
        allowed_domains=["shippingwatch.com", "news.google.com"],
        paywall_likely=True,
        keep_broad=True,
        prefer_rss=True,
        rss_urls=[
            "https://shippingwatch.com/rss/seneste.rss",
            google_news_rss("site:shippingwatch.com (tanker OR VLCC OR Aframax OR Suezmax OR crude)"),
        ],
    ),
    Source(
        key="seatrade",
        name="Seatrade Maritime",
        name_zh="Seatrade Maritime",
        urls=["https://www.seatrade-maritime.com", "https://www.seatrade-maritime.com/tankers"],
        group="third_party",
        item_selectors=["article", ".teaser", ".card", "li"],
        title_selectors=["h2 a", "h3 a", "h2", "a"],
        link_selectors=["h2 a", "h3 a", "a"],
        date_selectors=["time", ".date"],
        excerpt_selectors=["p"],
        allowed_domains=["seatrade-maritime.com", "news.google.com"],
        rss_urls=[
            "https://www.seatrade-maritime.com/rss.xml",
            "https://www.seatrade-maritime.com/feed",
            google_news_rss("site:seatrade-maritime.com (tanker OR VLCC OR shipping OR crude OR freight)"),
        ],
    ),
    Source(
        key="splash247",
        name="Splash 24/7",
        name_zh="Splash 24/7",
        urls=["https://splash247.com", "https://splash247.com/category/sector/tankers/"],
        group="third_party",
        item_selectors=["article", ".post", ".entry", "li"],
        title_selectors=["h2 a", "h3 a", ".entry-title a", "a"],
        link_selectors=["h2 a", "h3 a", ".entry-title a", "a"],
        date_selectors=["time", ".date"],
        excerpt_selectors=["p", ".excerpt"],
        allowed_domains=["splash247.com"],
        rss_urls=[
            "https://splash247.com/feed/",
            "https://splash247.com/category/sector/tankers/feed/",
        ],
    ),
    Source(
        key="tradewinds",
        name="TradeWinds",
        name_zh="TradeWinds",
        urls=["https://www.tradewindsnews.com"],
        group="third_party",
        item_selectors=["article", ".teaser", ".card", "li"],
        title_selectors=["h2 a", "h3 a", "h2", "a"],
        link_selectors=["h2 a", "h3 a", "a"],
        date_selectors=["time", ".date"],
        excerpt_selectors=["p"],
        allowed_domains=["tradewindsnews.com", "news.google.com"],
        paywall_likely=True,
        prefer_rss=True,
        rss_urls=[
            google_news_rss(
                "site:tradewindsnews.com (tanker OR VLCC OR Aframax OR Suezmax OR crude OR charterer OR shipowner)"
            ),
        ],
    ),
    Source(
        key="hellenic",
        name="Hellenic Shipping News",
        name_zh="Hellenic Shipping News",
        urls=["https://www.hellenicshippingnews.com", "https://www.hellenicshippingnews.com/category/tankers/"],
        group="third_party",
        item_selectors=["article", ".post", ".td-module-container", "li"],
        title_selectors=["h2 a", "h3 a", ".entry-title a", "a"],
        link_selectors=["h2 a", "h3 a", ".entry-title a", "a"],
        date_selectors=["time", ".date"],
        excerpt_selectors=["p", ".td-excerpt"],
        allowed_domains=["hellenicshippingnews.com"],
        rss_urls=[
            "https://www.hellenicshippingnews.com/feed/",
            "https://www.hellenicshippingnews.com/category/tankers/feed/",
        ],
    ),
    Source(
        key="bairdmaritime",
        name="Baird Maritime",
        name_zh="Baird Maritime",
        urls=["https://www.bairdmaritime.com"],
        group="third_party",
        item_selectors=["article", ".c-card", ".post", "li"],
        title_selectors=["h2 a", "h3 a", "a"],
        link_selectors=["h2 a", "h3 a", "a"],
        date_selectors=["time", ".date"],
        excerpt_selectors=["p"],
        allowed_domains=["bairdmaritime.com", "news.google.com"],
        prefer_rss=True,
        rss_urls=[
            "https://www.bairdmaritime.com/stories.rss",
            google_news_rss("site:bairdmaritime.com (tanker OR VLCC OR oil OR freight OR newbuild)"),
        ],
    ),
    Source(
        key="sol",
        name="Shipping Online",
        name_zh="航运在线",
        urls=["https://www.sol.com.cn", "https://www.sol.com.cn/News/"],
        group="third_party",
        item_selectors=["article", ".news-item", ".list-item", "li", ".title"],
        title_selectors=["h2 a", "h3 a", "a"],
        link_selectors=["a"],
        date_selectors=["time", ".date", ".time"],
        excerpt_selectors=["p"],
        allowed_domains=["sol.com.cn", "news.google.com"],
        language="zh",
        rss_urls=[
            google_news_rss_zh("site:sol.com.cn 油轮 OR 原油 OR 运价 OR VLCC OR 租船"),
        ],
    ),
    Source(
        key="eworldship",
        name="eWorldShip",
        name_zh="国际船舶网",
        urls=["https://www.eworldship.com", "https://www.eworldship.com/html/shipbuilding/"],
        group="third_party",
        item_selectors=["article", ".news-item", "li", ".list"],
        title_selectors=["h2 a", "h3 a", "a"],
        link_selectors=["a"],
        date_selectors=["time", ".date"],
        excerpt_selectors=["p"],
        allowed_domains=["eworldship.com", "news.google.com"],
        language="zh",
        extra_keywords=["新造船", "订单", "船厂", "油轮", "交付", "拆解", "VLCC"],
        rss_urls=[
            google_news_rss_zh("site:eworldship.com 油轮 OR 新造船 OR 船厂 OR VLCC OR 订单"),
        ],
    ),
]
