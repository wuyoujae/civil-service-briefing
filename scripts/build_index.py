import html
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "archive"
DATA = ROOT / "data"
NOW = datetime.now(ZoneInfo("Asia/Shanghai"))


def load_briefs():
    briefs = []
    for html_path in sorted(ARCHIVE.glob("*.html"), reverse=True):
        date = html_path.stem
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            continue
        json_path = ARCHIVE / f"{date}.json"
        md_path = ARCHIVE / f"{date}.md"
        items = []
        if json_path.exists():
            try:
                items = json.loads(json_path.read_text(encoding="utf-8"))
            except Exception:
                items = []
        top = parse_top_from_markdown(md_path, items)
        source_counts = Counter(item.get("source", "未分类") for item in items)
        briefs.append(
            {
                "date": date,
                "html": f"archive/{date}.html",
                "markdown": f"archive/{date}.md" if md_path.exists() else "",
                "json": f"archive/{date}.json" if json_path.exists() else "",
                "count": len(items),
                "sources": dict(source_counts.most_common()),
                "top": top[:10],
            }
        )
    return briefs


def parse_top_from_markdown(md_path, fallback_items):
    if not md_path.exists():
        return fallback_items[:10]
    text = md_path.read_text(encoding="utf-8")
    block = text.split("## 今日头条 / 必读 10 条", 1)
    if len(block) < 2:
        return fallback_items[:10]
    block = block[1].split("## 按来源分组", 1)[0]
    matches = re.findall(r"^\d+\.\s+\[([^\]]+)\]\(([^)]+)\)", block, flags=re.M)
    by_url = {item.get("url"): item for item in fallback_items}
    top = []
    for title, url in matches[:10]:
        item = dict(by_url.get(url, {}))
        item.setdefault("title", title)
        item.setdefault("url", url)
        item.setdefault("source", "")
        top.append(item)
    return top or fallback_items[:10]


def render_index(briefs):
    latest = briefs[0] if briefs else None
    latest_cards = ""
    if latest:
        latest_cards = "\n".join(
            f'''<li><a href="{html.escape(item.get("url", ""))}" target="_blank" rel="noopener noreferrer">{html.escape(item.get("title", ""))}</a><span>{html.escape(item.get("source", ""))}</span></li>'''
            for item in latest["top"][:10]
        )
    rows = "\n".join(
        f'''<article class="day">
  <div>
    <h2><a href="{html.escape(brief["html"])}">{html.escape(brief["date"])}</a></h2>
    <p>{brief["count"]} 条 · {len(brief["sources"])} 个来源 · Markdown: <a href="{html.escape(brief["markdown"])}">下载/复制</a></p>
  </div>
  <a class="open" href="{html.escape(brief["html"])}">打开简报</a>
</article>'''
        for brief in briefs
    )
    manifest = html.escape(json.dumps(briefs, ensure_ascii=False))
    return f'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>公考晨间新闻简报目录</title>
  <style>
    :root {{
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #1f2933;
      --muted: #667085;
      --line: #d9dee7;
      --blue: #1f5fbf;
      --red: #b42318;
      --green: #087443;
      --shadow: 0 10px 24px rgba(16, 24, 40, .08);
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif; background: var(--bg); color: var(--text); line-height: 1.6; }}
    .wrap {{ width: min(1100px, calc(100% - 32px)); margin: 0 auto; }}
    header {{ background: #fff; border-bottom: 1px solid var(--line); }}
    .head {{ display: grid; grid-template-columns: 1fr auto; gap: 24px; align-items: end; padding: 30px 0 22px; }}
    h1 {{ margin: 0; font-size: clamp(28px, 4vw, 42px); line-height: 1.15; letter-spacing: 0; }}
    .sub {{ margin: 10px 0 0; color: var(--muted); }}
    .stats {{ display: grid; grid-template-columns: repeat(3, minmax(96px, 1fr)); gap: 10px; }}
    .stat {{ background: #f8fafc; border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; }}
    .stat strong {{ display: block; font-size: 24px; line-height: 1.2; }}
    .stat span {{ color: var(--muted); font-size: 12px; }}
    main {{ display: grid; grid-template-columns: minmax(0, 1fr) 360px; gap: 20px; padding: 22px 0 52px; align-items: start; }}
    section {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; box-shadow: var(--shadow); padding: 16px; }}
    .section-head {{ display: flex; justify-content: space-between; align-items: center; gap: 12px; border-bottom: 1px solid var(--line); padding-bottom: 10px; margin-bottom: 12px; }}
    .section-head h2 {{ margin: 0; font-size: 18px; letter-spacing: 0; }}
    .day {{ display: flex; justify-content: space-between; gap: 14px; align-items: center; border: 1px solid #e7ebf1; border-radius: 8px; padding: 14px; background: #fff; margin-bottom: 10px; }}
    .day h2 {{ margin: 0; font-size: 20px; letter-spacing: 0; }}
    .day p {{ margin: 4px 0 0; color: var(--muted); font-size: 14px; }}
    a {{ color: var(--blue); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .open {{ white-space: nowrap; border: 1px solid var(--blue); border-radius: 8px; padding: 8px 12px; font-weight: 700; }}
    .latest {{ position: sticky; top: 14px; }}
    .latest ol {{ padding-left: 22px; margin: 0; }}
    .latest li {{ margin: 0 0 10px; }}
    .latest li span {{ display: block; color: var(--muted); font-size: 12px; }}
    .note {{ color: var(--muted); font-size: 13px; margin-top: 14px; border-top: 1px solid var(--line); padding-top: 12px; }}
    @media (max-width: 880px) {{ .head, main {{ grid-template-columns: 1fr; }} .latest {{ position: static; }} }}
    @media (max-width: 540px) {{ .wrap {{ width: min(100% - 20px, 1100px); }} .stats {{ grid-template-columns: 1fr; }} .day {{ align-items: flex-start; flex-direction: column; }} }}
  </style>
</head>
<body>
  <header>
    <div class="wrap head">
      <div>
        <h1>公考晨间新闻简报目录</h1>
        <p class="sub">每个工作日北京时间 10:00 自动更新。最近生成：{html.escape(latest["date"]) if latest else "暂无"}。</p>
      </div>
      <div class="stats">
        <div class="stat"><strong>{len(briefs)}</strong><span>归档天数</span></div>
        <div class="stat"><strong>{latest["count"] if latest else 0}</strong><span>最新条目</span></div>
        <div class="stat"><strong>{len(latest["sources"]) if latest else 0}</strong><span>最新来源</span></div>
      </div>
    </div>
  </header>
  <main class="wrap">
    <section>
      <div class="section-head"><h2>每日归档</h2><span>HTML + Markdown</span></div>
      {rows or '<p class="note">暂无归档。</p>'}
    </section>
    <section class="latest">
      <div class="section-head"><h2>最新必读</h2><span>{html.escape(latest["date"]) if latest else ""}</span></div>
      <ol>{latest_cards}</ol>
      <p class="note">摘要和标签在每日 HTML 中；目录页只展示索引和最新头条。</p>
    </section>
  </main>
  <script type="application/json" id="brief-manifest">{manifest}</script>
</body>
</html>'''


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    briefs = load_briefs()
    (ROOT / "index.html").write_text(render_index(briefs), encoding="utf-8")
    (DATA / "briefs.json").write_text(json.dumps(briefs, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Built index with {len(briefs)} day(s)")


if __name__ == "__main__":
    main()
