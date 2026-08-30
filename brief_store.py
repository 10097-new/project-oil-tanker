"""简报落盘：按日期分目录，并生成网页+公众号合页。

目录约定（briefs/ 根下只留入口）:

  briefs/index.html              日期目录
  briefs/latest.html             跳到最近一天的合页
  briefs/YYYY-MM-DD/YYYY-MM-DD 新闻搜集.html
  briefs/YYYY-MM-DD/web/brief.md|brief.html|articles.json
  briefs/YYYY-MM-DD/wechat/brief.md|brief.html|articles.json|analysis.json
"""

from __future__ import annotations

import html
import re
import shutil
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import config

DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LEGACY_WEB_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.(md|json|html)$")
LEGACY_WX_RE = re.compile(r"^wechat-(\d{4}-\d{2}-\d{2})\.(md|json|html)$")
LEGACY_WX_ANALYSIS_RE = re.compile(r"^wechat-(\d{4}-\d{2}-\d{2})\.analysis\.json$")
ROOT_ENTRY_NAMES = {"index.html", "latest.html", "latest.md", "wechat-latest.md", "wechat-latest.html"}


def stamp_dir(stamp: str) -> Path:
    return config.BRIEFS_DIR / stamp


def web_dir(stamp: str) -> Path:
    return stamp_dir(stamp) / "web"


def wechat_dir(stamp: str) -> Path:
    return stamp_dir(stamp) / "wechat"


def web_md(stamp: str) -> Path:
    return web_dir(stamp) / "brief.md"


def web_html(stamp: str) -> Path:
    return web_dir(stamp) / "brief.html"


def web_json(stamp: str) -> Path:
    return web_dir(stamp) / "articles.json"


def wechat_md(stamp: str) -> Path:
    return wechat_dir(stamp) / "brief.md"


def wechat_html(stamp: str) -> Path:
    return wechat_dir(stamp) / "brief.html"


def wechat_json(stamp: str) -> Path:
    return wechat_dir(stamp) / "articles.json"


def wechat_analysis(stamp: str) -> Path:
    return wechat_dir(stamp) / "analysis.json"


def intel_md(stamp: str) -> Path:
    return stamp_dir(stamp) / "intel.md"


def intel_json(stamp: str) -> Path:
    return stamp_dir(stamp) / "intel.json"


def intel_html(stamp: str) -> Path:
    return stamp_dir(stamp) / "intel.html"


def combined_filename(stamp: str) -> str:
    return f"{stamp} 新闻搜集.html"


def combined_html(stamp: str) -> Path:
    return stamp_dir(stamp) / combined_filename(stamp)


def combined_href(stamp: str) -> str:
    return f"{stamp}/{quote(combined_filename(stamp))}"


def index_html() -> Path:
    return config.BRIEFS_DIR / "index.html"


def latest_html() -> Path:
    return config.BRIEFS_DIR / "latest.html"


def list_stamps() -> list[str]:
    if not config.BRIEFS_DIR.exists():
        return []
    stamps = []
    for path in config.BRIEFS_DIR.iterdir():
        if path.is_dir() and DATE_DIR_RE.match(path.name):
            stamps.append(path.name)
    return sorted(stamps)


def resolve_web_json(spec: str) -> Path:
    text = (spec or "").strip()
    path = Path(text)
    if path.exists():
        return path
    if DATE_DIR_RE.match(text):
        return web_json(text)
    name = path.name
    match = re.match(r"^(\d{4}-\d{2}-\d{2})\.json$", name)
    if match:
        return web_json(match.group(1))
    return path


def resolve_wechat_json(spec: str = "") -> Path:
    text = (spec or "").strip()
    if not text:
        return wechat_json(datetime.now().strftime("%Y-%m-%d"))
    path = Path(text)
    if path.exists():
        return path
    if DATE_DIR_RE.match(text):
        return wechat_json(text)
    match = re.match(r"^wechat-(\d{4}-\d{2}-\d{2})\.json$", Path(text).name)
    if match:
        return wechat_json(match.group(1))
    return path


def migrate_legacy() -> list[tuple[Path, Path]]:
    """把根目录旧文件挪进日期子目录。已在子目录的不动。"""
    config.BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
    moved: list[tuple[Path, Path]] = []
    for path in list(config.BRIEFS_DIR.iterdir()):
        if not path.is_file():
            continue
        dest: Path | None = None
        match = LEGACY_WEB_RE.match(path.name)
        if match:
            dest = {
                "md": web_md(match.group(1)),
                "json": web_json(match.group(1)),
                "html": web_html(match.group(1)),
            }[match.group(2)]
        match = LEGACY_WX_RE.match(path.name)
        if match:
            dest = {
                "md": wechat_md(match.group(1)),
                "json": wechat_json(match.group(1)),
                "html": wechat_html(match.group(1)),
            }[match.group(2)]
        match = LEGACY_WX_ANALYSIS_RE.match(path.name)
        if match:
            dest = wechat_analysis(match.group(1))
        if dest is None:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            path.unlink()
            moved.append((path, dest))
            continue
        shutil.move(str(path), str(dest))
        moved.append((path, dest))
    for name in ("latest.md", "wechat-latest.md", "wechat-latest.html"):
        leftover = config.BRIEFS_DIR / name
        if leftover.exists() and leftover.is_file():
            leftover.unlink()
    return moved


def _iframe(doc: str | None, empty: str) -> str:
    if not doc:
        return f"<p class='empty'>{html.escape(empty)}</p>"
    return (
        "<iframe class='pane' sandbox='allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox' "
        f"srcdoc='{html.escape(doc, quote=True)}'></iframe>"
    )


def render_combined(stamp: str) -> str:
    intel_doc = intel_html(stamp).read_text(encoding="utf-8") if intel_html(stamp).exists() else None
    web_doc = web_html(stamp).read_text(encoding="utf-8") if web_html(stamp).exists() else None
    wx_doc = wechat_html(stamp).read_text(encoding="utf-8") if wechat_html(stamp).exists() else None
    lib_href = "../library/index.html"
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(stamp)} 油轮行业情报</title>
<style>
:root {{ --bg:#f4f1ea; --paper:#fffcf6; --ink:#1c1916; --muted:#6b645c; --line:#ddd4c6; --accent:#8c3b2a; }}
* {{ box-sizing:border-box; }}
html, body {{ margin:0; padding:0; background:var(--bg); color:var(--ink); }}
body {{ font:16px/1.55 "Source Han Serif SC","Noto Serif SC","Songti SC","SimSun",Georgia,serif; }}
.wrap {{ max-width:1280px; margin:0 auto; padding:20px 16px 48px; }}
header {{ border-bottom:1px solid var(--line); padding-bottom:14px; margin-bottom:18px; }}
h1 {{ margin:0; font-size:24px; }}
.nav {{ margin:8px 0 0; font-size:14px; }}
a {{ color:var(--accent); }}
.block {{ margin-bottom:22px; }}
.block h2 {{ margin:0 0 8px; font-size:15px; letter-spacing:.06em; color:var(--muted); }}
.pane {{ width:100%; height:min(78vh, 920px); border:1px solid var(--line); background:#fff; }}
.empty {{ color:var(--muted); padding:24px; background:var(--paper); border:1px dashed var(--line); }}
</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>{html.escape(stamp)} 油轮行业情报</h1>
  <p class="nav"><a href="{html.escape(lib_href)}">打开知识库（检索 / 标签 / 双语）</a></p>
</header>
<section class="block">
  <h2>本日情报</h2>
  {_iframe(intel_doc, "这一天还没有结构化情报。采集完成后会自动生成。")}
</section>
<section class="block">
  <h2>网页采集</h2>
  {_iframe(web_doc, "这一天还没有网页简报。")}
</section>
<section class="block">
  <h2>公众号</h2>
  {_iframe(wx_doc, "这一天还没有公众号简报。")}
</section>
</div>
</body>
</html>
"""


def render_latest_redirect(stamp: str) -> str:
    href = combined_href(stamp)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="0; url={html.escape(href, quote=True)}">
<title>最新简报</title>
</head>
<body>
<p><a href="{html.escape(href, quote=True)}">打开 {html.escape(stamp)} 新闻搜集</a></p>
</body>
</html>
"""


def render_index() -> str:
    rows = []
    for stamp in reversed(list_stamps()):
        has_web = web_html(stamp).exists() or web_md(stamp).exists()
        has_wx = wechat_html(stamp).exists() or wechat_md(stamp).exists()
        bits = []
        bits.append(f"<a href='{html.escape(combined_href(stamp))}'>合页</a>")
        if has_web:
            bits.append(f"<a href='{html.escape(stamp)}/web/brief.html'>网页</a>")
        if has_wx:
            bits.append(f"<a href='{html.escape(stamp)}/wechat/brief.html'>公众号</a>")
        if intel_html(stamp).exists():
            bits.append(f"<a href='{html.escape(stamp)}/intel.html'>本日情报</a>")
        flags = []
        if has_web:
            flags.append("网页")
        if has_wx:
            flags.append("公众号")
        flag_text = " + ".join(flags) if flags else "空"
        rows.append(
            f"<div class='row'><b>{html.escape(stamp)}</b> "
            f"<span class='muted'>{html.escape(flag_text)}</span> "
            + " · ".join(bits)
            + "</div>"
        )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>简报目录</title>
<style>
:root {{ --bg:#f4f1ea; --paper:#fffcf6; --ink:#1c1916; --muted:#6b645c; --line:#ddd4c6; --accent:#8c3b2a; }}
body {{ margin:0; background:var(--bg); color:var(--ink);
  font:16px/1.6 "Source Han Serif SC","Noto Serif SC","Songti SC",Georgia,serif; }}
a {{ color:var(--accent); text-decoration:none; }}
.wrap {{ max-width:820px; margin:0 auto; padding:28px 16px 64px; }}
h1 {{ margin:0 0 8px; }}
.muted {{ color:var(--muted); font-size:13px; }}
.row {{ background:var(--paper); border:1px solid var(--line); padding:12px 14px; margin:0 0 8px; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="muted"><a href="latest.html">最新合页</a> · <a href="library/index.html">知识库</a></div>
  <h1>油轮行业情报</h1>
  <p class="muted">按日期查看日报；知识库支持检索、标签、时间与中英切换，同一新闻不会跨日期重复。</p>
  {''.join(rows) or "<p class='muted'>还没有简报。</p>"}
</div>
</body>
</html>
"""


def publish_day(stamp: str) -> Path:
    """根据当天已有的网页/公众号 HTML 写合页、目录和 latest 入口。"""
    migrate_legacy()
    stamp_dir(stamp).mkdir(parents=True, exist_ok=True)
    out = combined_html(stamp)
    out.write_text(render_combined(stamp), encoding="utf-8")
    leftover = stamp_dir(stamp) / "combined.html"
    if leftover.exists() and leftover.resolve() != out.resolve():
        leftover.unlink()
    stamps = list_stamps()
    newest = stamps[-1] if stamps else stamp
    latest_html().write_text(render_latest_redirect(newest), encoding="utf-8")
    index_html().write_text(render_index(), encoding="utf-8")
    return out
