"""要盯的微信公众号。网页站走 crawl.py；公众号正文走本地 WeWe RSS。

feed_id 在 WeWe RSS 后台添加公众号后填写（形如 MP_WXS_xxxx）。
留空时按公众号名称从 /feeds/all.json 匹配。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class WechatAccount:
    key: str
    name: str
    feed_id: str = ""
    shipping: bool = True  # True：航运/能源号，过滤略宽


ACCOUNTS: list[WechatAccount] = [
    WechatAccount("lloydslist", "LloydsList"),
    WechatAccount("crsl", "克拉克森研究CRSL"),
    WechatAccount("broker", "海运经纪"),
    WechatAccount("jlc", "金联创订阅号"),
    WechatAccount("reuters", "路透财经早报", shipping=False),
    WechatAccount("kpler", "Kpler APAC"),
    WechatAccount("argus", "阿格斯Argus"),
]


def by_name() -> dict[str, WechatAccount]:
    return {item.name: item for item in ACCOUNTS}


# 名称变体，避免 WeWe 里显示名与名单不完全一致
NAME_ALIASES: dict[str, str] = {
    "lloyd's list": "LloydsList",
    "lloyds list": "LloydsList",
    "劳氏日报": "LloydsList",
    "克拉克森研究": "克拉克森研究CRSL",
    "clarksons": "克拉克森研究CRSL",
    "金联创": "金联创订阅号",
    "路透财经早报": "路透财经早报",
    "路透早报": "路透财经早报",
    "reuters morning": "路透财经早报",
    "kpler": "Kpler APAC",
    "argus": "阿格斯Argus",
    "阿格斯": "阿格斯Argus",
}
