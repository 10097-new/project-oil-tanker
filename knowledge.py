"""油轮行业知识库静态页：检索、标签、时间、双语。PC/移动端同一套页面。"""

from __future__ import annotations

import html
import json
from datetime import datetime
from typing import Any

from library import knowledge_html, library_dir
from taxonomy import CATEGORIES, SYNONYM_GROUPS


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def write_knowledge_html(articles: list[dict[str, Any]]) -> Any:
    library_dir()
    page = render_knowledge_html(articles)
    path = knowledge_html()
    path.write_text(page, encoding="utf-8")
    return path


def render_knowledge_html(articles: list[dict[str, Any]]) -> str:
    payload = []
    for item in articles:
        payload.append(
            {
                "title": item.get("title") or "",
                "title_zh": item.get("title_zh") or "",
                "title_en": item.get("title_en") or "",
                "url": item.get("url") or "",
                "source": item.get("source") or "",
                "date": item.get("date") or "",
                "published_at": item.get("published_at") or item.get("date") or "",
                "excerpt": item.get("excerpt") or "",
                "excerpt_zh": item.get("excerpt_zh") or "",
                "excerpt_en": item.get("excerpt_en") or "",
                "analysis": item.get("analysis") or "",
                "categories": item.get("categories") or [],
                "tags": item.get("tags") or [],
                "language": item.get("language") or "",
                "translated": bool(item.get("translated")),
                "related": item.get("related") or [],
                "group": item.get("group") or "",
            }
        )
    data_json = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    syn_json = json.dumps(SYNONYM_GROUPS, ensure_ascii=False).replace("<", "\\u003c")
    cat_json = json.dumps(CATEGORIES, ensure_ascii=False).replace("<", "\\u003c")
    year = datetime.now().year
    cat_buttons = "".join(
        f"<button type='button' class='tag' data-cat='{esc(c['key'])}'>{esc(c['zh'])}</button>"
        for c in CATEGORIES
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>油轮行业知识库</title>
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
.wrap {{ max-width:1120px; margin:0 auto; padding:20px 16px 72px; }}
header.hero {{
  border-bottom:1px solid var(--line); padding-bottom:14px; margin-bottom:16px;
  display:flex; justify-content:space-between; gap:16px; flex-wrap:wrap; align-items:flex-end;
}}
h1 {{ margin:0 0 4px; font-size:24px; font-weight:650; }}
.kicker {{ color:var(--muted); font-size:13px; }}
.lang {{ display:flex; gap:6px; }}
.lang button, .preset button, .tag {{
  font:13px/1.3 "Source Han Sans SC","Noto Sans SC",sans-serif;
  border:1px solid var(--line); background:#fff; color:var(--ink); padding:6px 10px; cursor:pointer;
}}
.lang button.on, .preset button.on, .tag.on {{
  background:var(--accent); color:#fff; border-color:var(--accent);
}}
.filters {{
  background:var(--paper); border:1px solid var(--line); padding:14px; margin-bottom:16px;
}}
.row {{ display:flex; flex-wrap:wrap; gap:8px; margin-bottom:10px; align-items:center; }}
.row label {{ color:var(--muted); font-size:13px; min-width:4.5em; }}
input[type=search], input[type=date] {{
  font:14px/1.4 "Source Han Sans SC","Noto Sans SC",sans-serif;
  border:1px solid var(--line); background:#fff; padding:7px 10px; min-width:0; flex:1;
}}
.tags {{ display:flex; flex-wrap:wrap; gap:6px; }}
.stats {{ color:var(--muted); font-size:13px; margin:0 0 12px; }}
.card {{
  background:#fff; border:1px solid var(--line); padding:14px 16px 12px; margin:0 0 12px;
}}
.card h2 {{ margin:0 0 8px; font-size:17px; font-family:"Source Han Sans SC","Noto Sans SC",sans-serif; }}
.meta {{ color:var(--muted); font-size:12px; display:flex; flex-wrap:wrap; gap:8px; margin-bottom:8px; align-items:center; }}
.badge {{
  font-size:11px; border:1px solid #e2c4bc; color:var(--accent); padding:0 5px; border-radius:3px;
}}
.excerpt {{ margin:0 0 8px; color:#3a342e; }}
.analysis {{ margin:0 0 8px; color:#3d3832; font-size:14px; }}
.pills {{ display:flex; flex-wrap:wrap; gap:6px; }}
.pill {{ font-size:12px; background:#f0e9dc; padding:2px 8px; color:#4a433c; cursor:pointer; border:0; }}
.related {{ font-size:13px; color:var(--muted); margin:8px 0 0; }}
.related a {{ margin-right:10px; }}
.empty {{ color:var(--muted); padding:28px; text-align:center; }}
.push {{ font-size:12px; color:var(--muted); margin-top:8px; }}
@media (max-width:720px) {{
  h1 {{ font-size:20px; }}
  .wrap {{ padding:14px 12px 80px; }}
}}
</style>
</head>
<body>
<div class="wrap">
<header class="hero">
  <div>
    <div class="kicker" data-i18n="kicker">可检索 · 可筛选 · 可追溯</div>
    <h1 data-i18n="title">油轮行业知识库</h1>
  </div>
  <div class="lang">
    <button type="button" id="lang-zh" class="on">中文</button>
    <button type="button" id="lang-en">EN</button>
  </div>
</header>
<section class="filters">
  <div class="row">
    <label data-i18n="search">检索</label>
    <input id="q" type="search" placeholder="关键词，如 招商轮船 / VLCC，支持同义词扩展">
  </div>
  <div class="row">
    <label data-i18n="time">时间</label>
    <div class="preset" id="presets">
      <button type="button" data-preset="24h">最近24小时</button>
      <button type="button" data-preset="week">最近一周</button>
      <button type="button" data-preset="month">最近一个月</button>
      <button type="button" data-preset="year"> {year}年全年</button>
      <button type="button" data-preset="all" class="on">全部</button>
    </div>
  </div>
  <div class="row">
    <label></label>
    <input id="from" type="date" title="开始">
    <input id="to" type="date" title="结束">
  </div>
  <div class="row">
    <label data-i18n="cats">分类</label>
    <div class="tags" id="cats">{cat_buttons}</div>
  </div>
  <p class="push" data-i18n="hint">多标签为「且」关系。已翻译条目带「译」。移动端可把本页加入主屏幕；重大新闻走企微/邮件推送。</p>
</section>
<p class="stats" id="stats"></p>
<div id="cards"></div>
</div>
<script id="payload" type="application/json">{data_json}</script>
<script id="synonyms" type="application/json">{syn_json}</script>
<script id="categories" type="application/json">{cat_json}</script>
<script>
const data = JSON.parse(document.getElementById("payload").textContent);
const synonyms = JSON.parse(document.getElementById("synonyms").textContent);
const categories = JSON.parse(document.getElementById("categories").textContent);
const catMap = Object.fromEntries(categories.map(c => [c.key, c]));
let lang = "zh";
const selectedCats = new Set();

function esc(value) {{
  return String(value ?? "").replace(/[&<>"']/g, ch => ({{
    "&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;","'":"&#39;"
  }})[ch]);
}}
function expandQuery(q) {{
  const raw = (q || "").trim();
  if (!raw) return [];
  const terms = [raw];
  const low = raw.toLowerCase();
  for (const group of synonyms) {{
    if (group.some(t => low.includes(String(t).toLowerCase()) || String(t).toLowerCase().includes(low))) {{
      terms.push(...group);
    }}
  }}
  return [...new Set(terms.map(t => String(t).toLowerCase()))];
}}
function queryGroups(q) {{
  return (q || "").trim().split(/\\s+/).filter(Boolean).map(expandQuery);
}}
function itemTime(item) {{
  const t = item.published_at || item.date || "";
  const d = new Date(t.replace(" ", "T"));
  return isNaN(d) ? null : d;
}}
function inRange(item) {{
  const from = document.getElementById("from").value;
  const to = document.getElementById("to").value;
  const t = (item.published_at || item.date || "").slice(0, 10);
  if (!t) return true;
  if (from && t < from) return false;
  if (to && t > to) return false;
  return true;
}}
function matchesQuery(item, groups) {{
  if (!groups.length) return true;
  const blob = [
    item.title, item.title_zh, item.title_en, item.excerpt, item.excerpt_zh, item.excerpt_en,
    item.source, item.analysis, (item.tags || []).join(" "), (item.categories || []).map(catLabel).join(" ")
  ].join(" ").toLowerCase();
  return groups.every(terms => terms.some(t => blob.includes(t)));
}}
function matchesCats(item) {{
  if (!selectedCats.size) return true;
  const have = new Set(item.categories || []);
  for (const key of selectedCats) {{
    if (!have.has(key)) return false;
  }}
  return true;
}}
function titleOf(item) {{
  return lang === "zh" ? (item.title_zh || item.title) : (item.title_en || item.title);
}}
function excerptOf(item) {{
  return lang === "zh" ? (item.excerpt_zh || item.excerpt) : (item.excerpt_en || item.excerpt);
}}
function catLabel(key) {{
  const c = catMap[key];
  if (!c) return key;
  return lang === "zh" ? c.zh : c.en;
}}
function render() {{
  const groups = queryGroups(document.getElementById("q").value);
  const items = data.filter(item => inRange(item) && matchesQuery(item, groups) && matchesCats(item));
  const stats = document.getElementById("stats");
  stats.textContent = (lang === "zh" ? "显示 " : "Showing ") + items.length + " / " + data.length;
  const box = document.getElementById("cards");
  if (!items.length) {{
    box.innerHTML = "<p class='empty'>" + (lang === "zh" ? "没有符合筛选的条目。" : "No matching items.") + "</p>";
    return;
  }}
  items.sort((a, b) => String(b.published_at || b.date).localeCompare(String(a.published_at || a.date)));
  box.innerHTML = items.map(item => {{
    const trans = item.translated ? "<span class='badge'>译</span>" : "";
    const cats = (item.categories || []).map(k =>
      "<button type='button' class='pill' data-cat='" + esc(k) + "'>" + esc(catLabel(k)) + "</button>"
    ).join("");
    const tags = (item.tags || []).map(t =>
      "<button type='button' class='pill' data-tag='" + esc(t) + "'>" + esc(t) + "</button>"
    ).join("");
    const related = (item.related || []).slice(0, 4).map(r =>
      "<a href='" + esc(r.url || "#") + "' target='_blank' rel='noopener'>" + esc((r.source || "") + " " + (r.title || "")) + "</a>"
    ).join("");
    const analysis = item.analysis
      ? "<p class='analysis'>" + esc((lang === "zh" ? "研判：" : "Implication: ") + item.analysis) + "</p>"
      : "";
    return "<article class='card'><h2><a href='" + esc(item.url) + "' target='_blank' rel='noopener'>"
      + esc(titleOf(item)) + "</a> " + trans + "</h2>"
      + "<div class='meta'><span>" + esc(item.source) + "</span><span>" + esc(item.published_at || item.date) + "</span></div>"
      + "<p class='excerpt'>" + esc(excerptOf(item)) + "</p>"
      + analysis
      + "<div class='pills'>" + cats + tags + "</div>"
      + (related ? "<div class='related'>" + (lang === "zh" ? "相关阅读：" : "Related: ") + related + "</div>" : "")
      + "</article>";
  }}).join("");
  box.querySelectorAll("[data-cat]").forEach(btn => btn.addEventListener("click", () => {{
    const key = btn.getAttribute("data-cat");
    document.querySelectorAll("#cats .tag").forEach(t => {{
      if (t.getAttribute("data-cat") === key) t.classList.toggle("on", true);
    }});
    selectedCats.add(key);
    render();
  }}));
  box.querySelectorAll("[data-tag]").forEach(btn => btn.addEventListener("click", () => {{
    document.getElementById("q").value = [document.getElementById("q").value, btn.getAttribute("data-tag") || ""]
      .filter(Boolean).join(" ").trim();
    render();
  }}));
}}
function setPreset(name) {{
  const from = document.getElementById("from");
  const to = document.getElementById("to");
  const now = new Date();
  const iso = d => d.toISOString().slice(0, 10);
  to.value = iso(now);
  if (name === "24h") {{
    const d = new Date(now.getTime() - 24*3600*1000);
    from.value = iso(d);
  }} else if (name === "week") {{
    const d = new Date(now.getTime() - 7*24*3600*1000);
    from.value = iso(d);
  }} else if (name === "month") {{
    const d = new Date(now.getTime() - 30*24*3600*1000);
    from.value = iso(d);
  }} else if (name === "year") {{
    from.value = "{year}-01-01";
    to.value = "{year}-12-31";
  }} else {{
    from.value = "";
    to.value = "";
  }}
}}
document.getElementById("lang-zh").addEventListener("click", () => {{
  lang = "zh";
  document.getElementById("lang-zh").classList.add("on");
  document.getElementById("lang-en").classList.remove("on");
  document.querySelectorAll("#cats .tag").forEach(btn => {{
    const c = catMap[btn.getAttribute("data-cat")];
    if (c) btn.textContent = c.zh;
  }});
  render();
}});
document.getElementById("lang-en").addEventListener("click", () => {{
  lang = "en";
  document.getElementById("lang-en").classList.add("on");
  document.getElementById("lang-zh").classList.remove("on");
  document.querySelectorAll("#cats .tag").forEach(btn => {{
    const c = catMap[btn.getAttribute("data-cat")];
    if (c) btn.textContent = c.en;
  }});
  render();
}});
document.querySelectorAll("#cats .tag").forEach(btn => btn.addEventListener("click", () => {{
  const key = btn.getAttribute("data-cat");
  if (selectedCats.has(key)) {{ selectedCats.delete(key); btn.classList.remove("on"); }}
  else {{ selectedCats.add(key); btn.classList.add("on"); }}
  render();
}}));
document.querySelectorAll("#presets button").forEach(btn => btn.addEventListener("click", () => {{
  document.querySelectorAll("#presets button").forEach(b => b.classList.remove("on"));
  btn.classList.add("on");
  setPreset(btn.getAttribute("data-preset"));
  render();
}}));
["q","from","to"].forEach(id => document.getElementById(id).addEventListener("input", render));
render();
</script>
</body>
</html>
"""
