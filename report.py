"""从 briefs/*.json 与同日 Markdown 生成对照用静态 HTML。只读 Markdown，不改其内容。"""

from __future__ import annotations

import argparse
import html
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import config

THEME_LABELS = {
    "oil_transport": "石油运输",
    "sanctions": "制裁",
    "non_tanker": "航运（非油轮）",
}

GROUP_LABELS = {
    "official": "官方",
    "third_party": "第三方",
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _looks_like_heading(text: str) -> bool:
    text = (text or "").strip()
    if not text or "\n" in text:
        return False
    if len(text) > 80:
        return False
    if text.endswith(("。", ".", "；", ";", "：", ":")):
        return False
    return True


def parse_digest(markdown: str) -> tuple[str, list[tuple[str, str]]]:
    text = (markdown or "").replace("\r\n", "\n").strip()
    if not text:
        return "", []
    lines = text.split("\n")
    title = lines[0].strip()
    rest = "\n".join(lines[1:]).strip()
    if not rest:
        return title, []
    blocks = [block.strip() for block in re.split(r"\n\s*\n", rest) if block.strip()]
    sections: list[tuple[str, str]] = []
    i = 0
    while i < len(blocks):
        heading, _, body = blocks[i].partition("\n")
        heading = heading.strip()
        body = body.strip()
        next_block = blocks[i + 1] if i + 1 < len(blocks) else ""
        if (
            not body
            and _looks_like_heading(heading)
            and next_block
            and not _looks_like_heading(next_block.split("\n", 1)[0])
        ):
            body = next_block
            i += 2
        else:
            i += 1
        if heading:
            sections.append((heading, body))
    return title, sections


def has_body(article: dict[str, Any]) -> bool:
    body = (article.get("body") or "").strip()
    if len(body) < 280:
        return False
    lowered = body.lower()
    if lowered.startswith("about treasury") and "economic d-day" not in lowered:
        return False
    return True


def body_label(article: dict[str, Any]) -> str:
    body = (article.get("body") or "").strip()
    if not body:
        return "仅标题"
    if has_body(article):
        return "有正文"
    return "摘要/片段"


def theme_label(theme: str) -> str:
    return THEME_LABELS.get(theme, theme or "未标注")


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def digest_html(title: str, sections: list[tuple[str, str]]) -> str:
    if not title and not sections:
        return "<p class='empty'>同日 Markdown 简报不存在，仅展示 JSON 条目。</p>"
    parts = []
    for heading, body in sections:
        parts.append("<section class='theme'>")
        parts.append(f"<h2>{esc(heading)}</h2>")
        if body:
            parts.append(f"<p>{esc(body)}</p>")
        parts.append("</section>")
    return "\n".join(parts) or f"<p class='empty'>{esc(title)}</p>"


def source_rows_html(sources: list[dict[str, Any]]) -> str:
    if not sources:
        return "<p class='empty'>本次 JSON 未记录各源采集状态。</p>"
    max_count = max((int(s.get("count") or 0) for s in sources), default=1) or 1
    rows = []
    for item in sources:
        count = int(item.get("count") or 0)
        kept = int(item.get("kept") or 0)
        ok = bool(item.get("ok"))
        parsed_w = 100.0 * count / max_count
        kept_w = 100.0 * kept / max_count
        note = item.get("note") or item.get("error") or "—"
        note = re.sub(r"\s+", " ", str(note)).strip()
        if len(note) > 80:
            note = note[:77] + "…"
        state = "成功" if ok else "失败"
        state_cls = "ok" if ok else "bad"
        rows.append(
            f"""
            <tr>
              <td>{esc(item.get("name_zh") or item.get("key"))}</td>
              <td>
                <div class="track" title="解析 {count} / 入选 {kept}">
                  <span class="bar parsed" style="width:{parsed_w:.1f}%"></span>
                  <span class="bar kept" style="width:{kept_w:.1f}%"></span>
                </div>
              </td>
              <td class="num">{count}</td>
              <td class="num">{kept}</td>
              <td><span class="pill {state_cls}">{state}</span></td>
              <td class="note">{esc(note)}</td>
            </tr>
            """
        )
    return "".join(rows)


def keyword_chips(articles: list[dict[str, Any]]) -> str:
    counter: Counter[str] = Counter()
    for article in articles:
        for word in article.get("matched_keywords") or []:
            if word:
                counter[str(word)] += 1
    if not counter:
        return ""
    chips = []
    for word, n in counter.most_common(18):
        chips.append(f"<span class='chip'>{esc(word)} <b>{n}</b></span>")
    return "<div class='chips'>" + "".join(chips) + "</div>"


def page_css() -> str:
    return """
:root {
  --bg: #f4f1ea;
  --paper: #fffcf6;
  --ink: #1c1916;
  --muted: #6b645c;
  --line: #ddd4c6;
  --accent: #8c3b2a;
  --official: #2f5d50;
  --ok: #2f5d50;
  --bad: #8c3b2a;
  --title-only: #8a7a63;
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; background: var(--bg); color: var(--ink); }
body {
  font: 16px/1.65 "Source Han Serif SC", "Noto Serif SC", "Songti SC", "SimSun", Georgia, serif;
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
.wrap { max-width: 1280px; margin: 0 auto; padding: 28px 20px 64px; }
header.hero {
  display: flex; justify-content: space-between; gap: 24px; align-items: flex-end;
  border-bottom: 1px solid var(--line); padding-bottom: 18px; margin-bottom: 22px;
}
header.hero h1 { margin: 0 0 6px; font-size: 28px; font-weight: 650; letter-spacing: .02em; }
.kicker { color: var(--muted); font-size: 13px; letter-spacing: .08em; }
.stats { display: flex; gap: 10px; flex-wrap: wrap; }
.stat {
  background: var(--paper); border: 1px solid var(--line); min-width: 92px;
  padding: 8px 12px 7px; text-align: right;
}
.stat b { display: block; font-size: 22px; line-height: 1.2; }
.stat span { color: var(--muted); font-size: 12px; }
.panel {
  background: var(--paper); border: 1px solid var(--line); padding: 16px 18px 12px; margin-bottom: 18px;
}
.panel h2 { margin: 0 0 12px; font-size: 15px; letter-spacing: .06em; color: var(--muted); font-weight: 650; }
.sources-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; margin: 0 -4px 8px; padding-bottom: 6px; }
.sources-wrap::-webkit-scrollbar { height: 8px; }
.sources-wrap::-webkit-scrollbar-thumb { background: #d7cbb8; border-radius: 4px; }
.sources-wrap::-webkit-scrollbar-track { background: #eee7db; }
table.sources { width: 100%; min-width: 620px; border-collapse: collapse; font-size: 14px; }
table.sources th, table.sources td { text-align: left; padding: 7px 8px; border-bottom: 1px solid var(--line); vertical-align: middle; }
table.sources th { color: var(--muted); font-size: 12px; font-weight: 650; }
td.num { text-align: right; font-variant-numeric: tabular-nums; width: 44px; }
td.note { color: var(--muted); font-size: 12px; max-width: 280px; }
.track {
  position: relative; height: 10px; background: #eee7db; width: 140px; min-width: 140px;
}
.bar { position: absolute; left: 0; top: 0; height: 10px; }
.bar.parsed { background: #d7cbb8; }
.bar.kept { background: var(--official); }
.pill { font-size: 12px; padding: 1px 7px; border: 1px solid var(--line); }
.pill.ok { color: var(--ok); border-color: #b7cfc6; }
.pill.bad { color: var(--bad); border-color: #e2c4bc; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { font-size: 12px; background: #f0e9dc; padding: 2px 8px; color: var(--muted); }
.chip b { color: var(--ink); }
.compare {
  display: grid; grid-template-columns: 1.05fr .95fr; gap: 18px; align-items: stretch;
  height: min(72vh, 820px);
}
.compare > .col.panel {
  display: flex; flex-direction: column; height: 100%; min-height: 0;
  margin-bottom: 0; overflow: hidden;
}
.col-body {
  flex: 1; min-height: 0; overflow-y: auto; padding-right: 8px;
  overscroll-behavior: contain;
}
.col-body::-webkit-scrollbar { width: 8px; }
.col-body::-webkit-scrollbar-thumb { background: #d7cbb8; }
.col-body::-webkit-scrollbar-track { background: #eee7db; }
.digest .theme {
  background: #fff;
  border: 1px solid var(--line);
  border-left: 4px solid var(--accent);
  padding: 12px 14px 14px;
  margin: 0 0 14px;
}
.digest .theme h2 {
  font: 650 1.05rem/1.4 "Source Han Sans SC", "Noto Sans SC", sans-serif;
  color: var(--accent);
  margin: 0 0 10px;
  padding-bottom: 8px;
  border-bottom: 1px dashed var(--line);
  letter-spacing: 0;
}
.digest .theme p { margin: 0; color: #3a342e; font-size: .98rem; }
.digest .terms {
  background: #fff;
  border: 1px solid var(--line);
  padding: 12px 14px 14px;
  margin: 0 0 14px;
}
@media (max-width: 980px) {
  .compare { grid-template-columns: 1fr; height: auto; }
  .compare > .col.panel { height: min(60vh, 640px); margin-bottom: 18px; }
}
.toolbar {
  display: grid; grid-template-columns: 1fr 1fr; gap: 8px;
  margin-bottom: 12px; flex-shrink: 0;
}
.toolbar input, .toolbar select {
  width: 100%; min-width: 0;
  font: 14px/1.4 "Source Han Sans SC", "Noto Sans SC", sans-serif;
  border: 1px solid var(--line); background: #fff; padding: 6px 8px; color: var(--ink);
}
.card {
  border: 1px solid var(--line); padding: 12px 13px 10px; margin-bottom: 10px; background: #fff;
}
.card h3 { margin: 0 0 6px; font-size: 15px; font-family: "Source Han Sans SC", "Noto Sans SC", sans-serif; }
.meta { color: var(--muted); font-size: 12px; display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 6px; }
.excerpt { font-size: 13px; color: #3d3832; margin: 0; }
.empty { color: var(--muted); }
.hidden { display: none; }
.index a { display: block; padding: 8px 0; border-bottom: 1px solid var(--line); }
"""


def article_payload(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for i, article in enumerate(articles):
        themes = article.get("themes") or []
        rows.append(
            {
                "i": i,
                "title": article.get("title") or "",
                "url": article.get("url") or "",
                "source": article.get("source") or "",
                "group": GROUP_LABELS.get(article.get("group") or "", article.get("group") or ""),
                "group_key": article.get("group") or "",
                "date": article.get("date") or "",
                "excerpt": (article.get("excerpt") or "")[:280],
                "themes": [theme_label(t) for t in themes],
                "theme_keys": themes,
                "keywords": article.get("matched_keywords") or [],
                "body": body_label(article),
            }
        )
    return rows


def render_day_html(payload: dict[str, Any], markdown: str, index_href: str = "index.html") -> str:
    date = payload.get("date") or ""
    articles = payload.get("articles") or []
    sources = payload.get("sources") or []
    title, sections = parse_digest(markdown)
    official = sum(1 for a in articles if a.get("group") == "official")
    third = sum(1 for a in articles if a.get("group") == "third_party")
    with_body = sum(1 for a in articles if has_body(a))
    data_json = json.dumps(article_payload(articles), ensure_ascii=False).replace("<", "\\u003c")
    source_options = sorted({a.get("source") or "" for a in articles if a.get("source")})
    source_opts = "".join(f"<option value='{esc(name)}'>{esc(name)}</option>" for name in source_options)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(date)} 网页信息简报</title>
<style>{page_css()}</style>
</head>
<body>
<div class="wrap">
<header class="hero">
  <div>
    <h1>{esc(date)} 网页信息简报</h1>
  </div>
  <div class="stats">
    <div class="stat"><b>{len(articles)}</b><span>入选条目</span></div>
    <div class="stat"><b>{official}</b><span>官方</span></div>
    <div class="stat"><b>{third}</b><span>第三方</span></div>
    <div class="stat"><b>{with_body}</b><span>有正文</span></div>
    <div class="stat"><b>{len(articles) - with_body}</b><span>标题/摘要</span></div>
  </div>
</header>

<section class="panel">
  <h2>各源解析 / 入选</h2>
  <div class="sources-wrap">
  <table class="sources">
    <thead>
      <tr><th>来源</th><th>占比</th><th>解析</th><th>入选</th><th>状态</th><th>备注</th></tr>
    </thead>
    <tbody>
      {source_rows_html(sources)}
    </tbody>
  </table>
  </div>
  {keyword_chips(articles)}
</section>

<div class="compare">
  <div class="col panel">
    <h2 class="col-title">现有 Markdown 简报</h2>
    <div class="digest col-body">
      {digest_html(title, sections)}
    </div>
  </div>
  <div class="col panel">
    <h2 class="col-title">JSON 入选条目 <span id="shown-count"></span></h2>
    <div class="toolbar">
      <input id="q" type="search" placeholder="搜索标题 / 关键词">
      <select id="source"><option value="">全部来源</option>{source_opts}</select>
      <select id="group">
        <option value="">官方+第三方</option>
        <option value="official">仅官方</option>
        <option value="third_party">仅第三方</option>
      </select>
      <select id="body">
        <option value="">正文不限</option>
        <option value="有正文">有正文</option>
        <option value="仅标题">仅标题</option>
        <option value="摘要/片段">摘要/片段</option>
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
  const group = document.getElementById("group").value;
  const body = document.getElementById("body").value;
  if (source && item.source !== source) return false;
  if (group && item.group_key !== group) return false;
  if (body && item.body !== body) return false;
  if (!q) return true;
  const blob = [item.title, item.excerpt, item.source, (item.keywords || []).join(" ")].join(" ").toLowerCase();
  return blob.includes(q);
}}
function render() {{
  const items = data.filter(matches);
  shown.textContent = "（" + items.length + "/" + data.length + "）";
  cards.innerHTML = items.map(item => {{
    const tags = (item.themes || []).concat([item.body]).map(t => "<span class='pill'>" + esc(t) + "</span>").join(" ");
    const excerpt = item.excerpt ? "<p class='excerpt'>" + esc(item.excerpt) + "</p>" : "";
    return "<article class='card'><h3><a href='" + esc(item.url) + "' target='_blank' rel='noopener'>" + esc(item.title) + "</a></h3>"
      + "<div class='meta'><span>" + esc(item.source) + "</span><span>" + esc(item.group) + "</span><span>" + esc(item.date || "") + "</span>" + tags + "</div>"
      + excerpt + "</article>";
  }}).join("") || "<p class='empty'>没有符合筛选的条目。</p>";
}}
["q","source","group","body"].forEach(id => document.getElementById(id).addEventListener("input", render));
render();
</script>
</body>
</html>
"""


def json_files() -> list[Path]:
    import brief_store

    brief_store.migrate_legacy()
    return [brief_store.web_json(stamp) for stamp in brief_store.list_stamps() if brief_store.web_json(stamp).exists()]


def markdown_for(date: str) -> str:
    import brief_store

    path = brief_store.web_md(date)
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def write_index(paths: list[Path]) -> Path:
    import brief_store

    brief_store.migrate_legacy()
    stamps = []
    for path in paths:
        payload = load_json(path)
        stamps.append(payload.get("date") or path.parent.parent.name)
    newest = stamps[-1] if stamps else (brief_store.list_stamps()[-1] if brief_store.list_stamps() else datetime.now().strftime("%Y-%m-%d"))
    if stamps:
        brief_store.publish_day(newest)
    else:
        config.BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
        brief_store.index_html().write_text(brief_store.render_index(), encoding="utf-8")
    return brief_store.index_html()


def write_day(json_path: Path) -> Path:
    import brief_store

    payload = load_json(json_path)
    date = payload.get("date") or json_path.parent.parent.name
    html_path = brief_store.web_html(date)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(render_day_html(payload, markdown_for(date), index_href="../../index.html"), encoding="utf-8")
    brief_store.publish_day(date)
    return html_path


def write_all() -> list[Path]:
    config.BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
    written = [write_day(path) for path in json_files()]
    write_index(json_files())
    return written


def write_from_payload(payload: dict[str, Any], markdown: str) -> Path:
    """采集流程调用：根据刚写入的 JSON/Markdown 生成对照页，不改 Markdown。"""
    import brief_store

    brief_store.migrate_legacy()
    date = payload.get("date") or datetime.now().strftime("%Y-%m-%d")
    html_path = brief_store.web_html(date)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(render_day_html(payload, markdown, index_href="../../index.html"), encoding="utf-8")
    brief_store.publish_day(date)
    return html_path


def main() -> None:
    parser = argparse.ArgumentParser(description="生成 Markdown/JSON 对照用静态 HTML")
    parser.add_argument("--date", default="", help="只生成某一天，如 2026-08-25")
    args = parser.parse_args()
    if args.date:
        import brief_store

        path = brief_store.resolve_web_json(args.date)
        if not path.exists():
            raise SystemExit(f"找不到 {path}")
        html_path = write_day(path)
        print(html_path)
        return
    written = write_all()
    print("已生成:")
    for path in written:
        print(f"  {path}")
    print(f"  {config.BRIEFS_DIR / 'index.html'}")
    print(f"  {config.BRIEFS_DIR / 'latest.html'}")


if __name__ == "__main__":
    main()
