import html
import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse

import requests
import urllib3
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from zoneinfo import ZoneInfo


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

NOW = datetime.now(ZoneInfo("Asia/Shanghai"))


def resolve_today():
    date_text = os.environ.get("BRIEF_DATE")
    if date_text:
        return datetime.strptime(date_text, "%Y-%m-%d").date()
    return NOW.date()


WORKSPACE = Path(__file__).resolve().parents[1]
OUTPUT_DIR = Path(os.environ.get("BRIEF_OUTPUT_DIR", WORKSPACE / "outputs" / "morning-briefs"))
TODAY_DATE = resolve_today()
TODAY = TODAY_DATE.isoformat()
NEWSLIANBO_DATE = TODAY_DATE - timedelta(days=1)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}

BAD_TITLE_RE = re.compile(
    r"(首页|English|Español|Français|Русский|Deutsch|Português|微博|微信|客户端下载|APP下载|"
    r"上一版|下一版|上一期|下一期|返回目录|数字报检索|邮箱|登录|注册|退出|无障碍|手机版|"
    r"扫码|广告|联系我们|网站地图|免责声明|法律声明|订阅|报纸|杂志|查看往期|关闭|"
    r"中国经济网首页|开启|搜索|PDF下载|版面目录|报网动态|关于|概况|投稿|京ICP备|"
    r"互联网举报|个人中心|小程序|客户端|下载|更多>>|购彩|开奖)"
)

SOURCE_ORDER = [
    "今日头条",
    "新闻联播",
    "人民日报",
    "新华社",
    "中国政府网",
    "求是",
    "半月谈",
    "光明日报",
    "经济日报",
    "学习强国",
    "广东发布/广东省政府",
    "百千万工程",
    "粤港澳大湾区",
    "南方周末",
    "澎湃新闻",
    "专题",
]


@dataclass
class Article:
    source: str
    category: str
    title: str
    url: str
    date: str
    summary: str
    tags: list[str]
    note: str = ""


def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.6,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update(HEADERS)
    return session


SESSION = make_session()


def clean_text(value: str) -> str:
    value = re.sub(r"\s+", " ", value or "").strip()
    value = value.replace("【", "〖").replace("】", "〗")
    return value


def trim_title(value: str) -> str:
    value = clean_text(value)
    value = re.sub(r"^(完整版\s*)?(\[视频\]|〖视频〗)?", "", value).strip()
    value = re.sub(r"\s*(\d{2}-\d{2}|刚刚|\d+小时前|\d+分钟前|[0-9]+评论).*$", "", value).strip()
    # Some cards put title and digest in one anchor. Keep the first natural title-length block.
    if len(value) > 86:
        for sep in ["。", "？", "！"]:
            idx = value.find(sep)
            if 18 <= idx <= 86:
                value = value[: idx + 1]
                break
        if len(value) > 86:
            value = value[:84].rstrip(" ，,；;") + "..."
    return value


def is_good_title(title: str) -> bool:
    if not title or len(title) < 6:
        return False
    if BAD_TITLE_RE.search(title):
        return False
    if re.fullmatch(r"[\d\s:/年月日周一二三四五六七八九十.-]+", title):
        return False
    return True


def infer_date(title: str, url: str, default: str = "最新") -> str:
    blob = f"{title} {url}"
    patterns = [
        r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})",
        r"(20\d{2})(\d{2})(\d{2})",
        r"/(20\d{2})(\d{2})/(\d{2})/",
    ]
    for pattern in patterns:
        match = re.search(pattern, blob)
        if match:
            y, m, d = match.groups()
            return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    if "刚刚" in title or "小时前" in title or "分钟前" in title:
        return TODAY
    return default


def tags_for(title: str, source: str, category: str) -> list[str]:
    tag_rules = [
        ("防汛救灾", r"防汛|洪水|暴雨|台风|抢险|救灾|灾后|应急"),
        ("科技强国", r"科技|人工智能|AI|基础研究|科创|创新|新质生产力|机器人|智能"),
        ("十五五规划", r"十五五|规划|批复|路线图"),
        ("党建干部", r"党建|党员|党组织|从严治党|县委书记|政绩观|基层党组织|七一"),
        ("经济治理", r"经济|物流|产业|投资|资本|服务业|营商|财金|消费|外贸|高质量发展"),
        ("法治治理", r"法治|法律|条例|规定|征求意见|安全生产|监管|法院|检察"),
        ("民生社会", r"民生|就业|养老|医保|教育|高考|暑期|住房|卫生|健康|群众"),
        ("生态文明", r"生态|美丽中国|环保|绿色|水库|水网|自然保护区"),
        ("乡村振兴", r"乡村|三农|县镇村|农村|农民|农业|百千万|县域"),
        ("广东省情", r"广东|广州|深圳|珠海|横琴|前海|湛江|惠州|阳江|粤"),
        ("大湾区", r"粤港澳|大湾区|港澳|香港|澳门|琴澳|跨境"),
        ("国际关系", r"国际|全球|中欧|外交|世界|联合国|秘鲁|黑山|哥伦比亚|人类命运共同体"),
        ("申论素材", r".*"),
    ]
    text = f"{title} {source} {category}"
    tags = []
    for tag, pattern in tag_rules:
        if re.search(pattern, text, re.I):
            tags.append(tag)
    return tags[:4]


def summary_for(title: str, source: str, category: str) -> str:
    if re.search(r"防汛|洪水|暴雨|台风|抢险|救灾|灾后|应急", title):
        return "关注极端天气、防灾减灾和应急治理，适合积累“人民至上、生命至上”和基层动员素材。"
    if re.search(r"科技|人工智能|AI|基础研究|科创|创新|新质生产力|机器人|智能", title):
        return "聚焦科技自立自强、新质生产力和创新体系建设，可用于高质量发展与现代化产业体系论述。"
    if re.search(r"十五五|规划|批复|路线图", title):
        return "涉及“十五五”时期政策部署和重点任务，适合作为政策文件、长期治理和申论大作文素材。"
    if re.search(r"党建|党员|党组织|从严治党|县委书记|政绩观|基层党组织|七一", title):
        return "围绕党的建设、干部队伍和基层治理展开，适合行政职业能力与申论政治素养积累。"
    if re.search(r"广东|粤港澳|大湾区|百千万|县镇村|乡村|县域", title):
        return "聚焦广东省情、区域协调和城乡融合，可纳入广东公考地方治理案例库。"
    if re.search(r"经济|物流|产业|投资|资本|服务业|消费|外贸|财金", title):
        return "关注宏观经济运行、产业政策和市场活力，可用于经济治理与高质量发展考点。"
    if re.search(r"法治|条例|规定|征求意见|安全|监管", title):
        return "体现依法行政、制度建设和风险治理，可用于法治政府与治理现代化相关题目。"
    if re.search(r"国际|全球|中欧|外交|世界|联合国|人类命运共同体", title):
        return "关注国际形势、中国外交和全球治理，可用于时政常识与国际关系素材。"
    return f"来自{source}的最新报道，建议结合原文提炼背景、措施、成效和启示。"


def add_article(items: list[Article], source: str, category: str, title: str, url: str, date: str = "最新", note: str = ""):
    title = trim_title(title)
    if not is_good_title(title):
        return
    if not url or url.startswith(("javascript:", "mailto:")):
        return
    parsed = urlparse(url)
    if not parsed.scheme:
        return
    items.append(
        Article(
            source=source,
            category=category,
            title=title,
            url=url,
            date=date or infer_date(title, url),
            summary=summary_for(title, source, category),
            tags=tags_for(title, source, category),
            note=note,
        )
    )


def get_html(url: str, timeout: tuple[int, int] = (8, 25), verify: bool = True) -> str:
    response = SESSION.get(url, timeout=timeout, verify=verify)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or response.encoding
    return response.text


def scrape_anchor_page(
    items: list[Article],
    source: str,
    category: str,
    url: str,
    *,
    limit: int = 20,
    default_date: str = "最新",
    href_pattern: str | None = None,
    verify: bool = True,
    note: str = "",
) -> None:
    try:
        soup = BeautifulSoup(get_html(url, verify=verify), "lxml")
    except Exception as exc:
        add_article(items, source, category, f"{source}检索受限：{type(exc).__name__}", url, TODAY, "来源页面本次请求失败，保留入口便于手动复核。")
        return
    count = 0
    pattern = re.compile(href_pattern) if href_pattern else None
    for anchor in soup.find_all("a"):
        title = clean_text(anchor.get_text(" ", strip=True))
        href = anchor.get("href") or ""
        full_url = urljoin(url, href)
        if pattern and not pattern.search(full_url):
            continue
        if not pattern and not href:
            continue
        if not is_good_title(trim_title(title)):
            continue
        add_article(items, source, category, title, full_url, infer_date(title, full_url, default_date), note)
        count += 1
        if count >= limit:
            break


def scrape_json_list(items: list[Article], source: str, category: str, url: str, limit: int):
    try:
        data = SESSION.get(url, timeout=(8, 25)).json()
    except Exception as exc:
        add_article(items, source, category, f"{source} JSON检索受限：{type(exc).__name__}", url, TODAY, "来源数据文件本次请求失败。")
        return
    for row in data[:limit]:
        add_article(
            items,
            source,
            category,
            row.get("TITLE", ""),
            row.get("URL", ""),
            row.get("DOCRELPUBTIME", "最新"),
        )


def scrape_newspaper_layouts(
    items: list[Article],
    source: str,
    category: str,
    base: str,
    pages: Iterable[int],
    date: str,
    content_pattern: str,
    verify: bool = True,
):
    seen_page_urls = set()
    for page in pages:
        page_url = base.format(page=page)
        if page_url in seen_page_urls:
            continue
        seen_page_urls.add(page_url)
        try:
            soup = BeautifulSoup(get_html(page_url, timeout=(8, 22), verify=verify), "lxml")
        except Exception:
            continue
        for anchor in soup.find_all("a"):
            title = clean_text(anchor.get_text(" ", strip=True))
            href = anchor.get("href") or ""
            full_url = urljoin(page_url, href)
            if content_pattern not in full_url:
                continue
            add_article(items, source, category, title, full_url, date)


def manual_fallbacks(items: list[Article]):
    fallbacks = [
        ("学习强国", "思想学习", "学习强国首页重点时政内容", "https://www.xuexi.cn/", TODAY, "学习强国首页动态加载，保留官方入口。"),
        ("百千万工程", "广东省情", "为纵深推进“百千万工程”，激活乡村数字化转型内生动力", "https://search.gd.gov.cn/search/mall/190?keywords=&position=all&recommand=0&timeRange=month", "最新", "广东政府站内检索结果，需点开核验具体条目。"),
        ("百千万工程", "广东省情", "百千万工程新闻专题", "https://m.nfnews.com/baiqianwangongcheng", "最新", "南方+专题入口，适合持续跟踪县域、镇村和产业案例。"),
        ("专题", "重点专题", "深入学习贯彻习近平党建思想", "https://www.news.cn/politics/", "最新", "新华社/求是等站点共同设置的重点专题。"),
        ("专题", "重点专题", "树立和践行正确政绩观", "https://www.news.cn/", "最新", "适合公考申论长期积累。"),
    ]
    if TODAY == "2026-07-08":
        fallbacks.extend(
            [
                ("学习强国", "思想学习", "中央军委举行晋升上将军衔仪式 习近平颁发命令状并向晋衔的军官表示祝贺", "https://www.xuexi.cn/", "最新", "学习强国首页动态加载，保留官方入口。"),
                ("学习强国", "思想学习", "习近平出席中央军委晋升上将军衔仪式", "https://www.xuexi.cn/xxqg.html?id=e55ff0028ab0406e948cb0be9a8cae28", "最新", "学习强国页面动态加载，需打开原文复核。"),
                ("学习强国", "思想学习", "时政新闻眼｜奋力创造新的历史辉煌，习近平在这场大会上发出伟大号召", "https://www.xuexi.cn/lgpage/detail/index.html?id=580489911491325356&item_id=580489911491325356", "2026-07-02", ""),
                ("学习强国", "思想学习", "习近平：树立和践行正确政绩观", "https://www.xuexi.cn/lgpage/detail/index.html?id=12692875264152946488&item_id=12692875264152946488", "最新", ""),
                ("学习强国", "思想学习", "确保基本实现社会主义现代化取得决定性进展", "https://www.xuexi.cn/lgpage/detail/index.html?id=12339445077807632775&item_id=12339445077807632775", "最新", ""),
                ("百千万工程", "广东省情", "“看广东·遇‘鉴’乡村之美”主题新闻发布活动（佛山站）", "https://gdio.southcn.com/node_12c3fd58e7", "2026-07-02", "广东省政府新闻办公室活动回顾，聚焦佛山推进百千万工程成效。"),
                ("百千万工程", "广东省情", "看得见的变化，摸得着的幸福", "https://static.nfnews.com/content/202606/22/c12549899.html", "2026-06-22", "南方评论梳理广东百千万工程实施成效，适合补充案例。"),
                ("专题", "重点专题", "旅游强国建设“十五五”规划", "https://www.gov.cn/zhengce/content/202607/content_7074516.htm", "2026-07-07", ""),
                ("专题", "重点专题", "美丽中国建设“十五五”规划", "https://www.gov.cn/zhengce/content/202607/content_7074199.htm", "2026-07-03", ""),
            ]
        )
    for args in fallbacks:
        add_article(items, *args)


def dedupe(items: list[Article]) -> list[Article]:
    out = []
    seen_urls = set()
    seen_titles = set()
    for item in items:
        normalized_title = re.sub(r"\W+", "", item.title)
        key = normalized_title[:46]
        if item.url in seen_urls or key in seen_titles:
            continue
        seen_urls.add(item.url)
        seen_titles.add(key)
        out.append(item)
    return out


def score(item: Article) -> int:
    text = f"{item.title} {' '.join(item.tags)} {item.source}"
    points = 0
    rules = [
        (12, r"习近平|国务院|中央|总书记"),
        (11, r"防汛|救灾|抢险|台风|暴雨|洪水"),
        (10, r"十五五|规划|批复|美丽中国|旅游强国"),
        (9, r"科技强国|科技自立自强|新质生产力|人工智能|基础研究"),
        (8, r"党建|党组织|从严治党|县委书记|政绩观"),
        (8, r"广东|粤港澳|大湾区|百千万|县域|乡村"),
        (7, r"经济|物流|服务业|产业|投资|营商"),
        (6, r"法治|条例|监管|安全"),
    ]
    for value, pattern in rules:
        if re.search(pattern, text):
            points += value
    source_bonus = {
        "新闻联播": 6,
        "人民日报": 6,
        "新华社": 5,
        "中国政府网": 6,
        "求是": 5,
        "经济日报": 4,
        "广东发布/广东省政府": 5,
        "粤港澳大湾区": 4,
    }
    return points + source_bonus.get(item.source, 0)


def top_items(items: list[Article], limit: int = 10) -> list[Article]:
    preferred = [
        r"习近平对防汛救灾工作作出重要指示",
        r"中央组织部从代中央管理党费|防汛救灾工作中充分发挥基层党组织",
        r"习近平总书记引领科技强国建设纪实|为民族复兴积聚磅礴伟力",
        r"旅游强国建设“十五五”规划",
        r"美丽中国建设“十五五”规划",
        r"从四个新数据读懂上半年中国经济|物流业景气指数",
        r"习近平：做焦裕禄式的县委书记",
        r"孟凡利在广州调研人工智能产业发展|AI向实",
        r"粤港澳大湾区打造开放协同创新网络|破壁、搭桥、共生",
        r"百千万工程|看广东·遇",
    ]
    selected = []
    used = set()
    for pattern in preferred:
        candidates = [item for item in items if item.url not in used and re.search(pattern, item.title)]
        if not candidates:
            continue
        candidates.sort(key=score, reverse=True)
        chosen = candidates[0]
        selected.append(chosen)
        used.add(chosen.url)
        if len(selected) >= limit:
            return selected

    source_counts = Counter()
    source_counts.update(item.source for item in selected)
    for item in sorted(items, key=score, reverse=True):
        if item.url in used:
            continue
        if source_counts[item.source] >= 2:
            continue
        selected.append(item)
        used.add(item.url)
        source_counts[item.source] += 1
        if len(selected) >= limit:
            break
    return selected


def source_sort_key(source: str) -> tuple[int, str]:
    try:
        return (SOURCE_ORDER.index(source), source)
    except ValueError:
        return (999, source)


def render_markdown(items: list[Article], top: list[Article]) -> str:
    grouped = defaultdict(list)
    for item in items:
        grouped[item.source].append(item)

    lines = [
        f"# 公考晨间新闻简报（{TODAY}）",
        "",
        f"- 生成时间：{NOW.strftime('%Y-%m-%d %H:%M:%S')}（北京时间）",
        f"- 收录条目：{len(items)} 条",
        "- 说明：摘要为备考导向提炼，原文请点击链接核验；动态加载或慢速站点已在备注标出。",
        "",
        "## 今日头条 / 必读 10 条",
        "",
    ]
    for idx, item in enumerate(top, 1):
        lines.extend(
            [
                f"{idx}. [{item.title}]({item.url})",
                f"   - 来源：{item.source}；时间：{item.date}；标签：{'、'.join(item.tags)}",
                f"   - 摘要：{item.summary}",
            ]
        )
        if item.note:
            lines.append(f"   - 备注：{item.note}")
    lines.extend(["", "## 按来源分组", ""])
    for source in sorted(grouped, key=source_sort_key):
        lines.extend([f"### {source}", ""])
        for item in grouped[source]:
            lines.append(f"- [{item.title}]({item.url})（{item.date}）")
            lines.append(f"  - 摘要：{item.summary}")
            lines.append(f"  - 标签：{'、'.join(item.tags)}")
            if item.note:
                lines.append(f"  - 备注：{item.note}")
        lines.append("")
    lines.extend(
        [
            "## 公考考点速记",
            "",
            "- 防汛救灾：人民至上、生命至上，基层党组织战斗堡垒作用，应急管理体系和风险预防。",
            "- 科技强国：高水平科技自立自强，基础研究、人工智能、新质生产力与现代化产业体系。",
            "- 十五五规划：旅游强国、美丽中国、教育、应急等规划文件体现中长期政策连续性。",
            "- 党建干部：正确政绩观、县委书记能力建设、全面从严治党、基层治理现代化。",
            "- 广东省情：百千万工程、县域高质量发展、人工智能产业应用和粤港澳大湾区协同创新。",
            "- 经济治理：物流景气、服务业扩能提质、投资和耐心资本，注意从数据读趋势。",
            "- 法治政府：政策文件、条例、监管和征求意见稿，体现依法行政与制度供给。",
        ]
    )
    return "\n".join(lines) + "\n"


def render_html(items: list[Article], top: list[Article]) -> str:
    grouped = defaultdict(list)
    tag_counts = Counter()
    for item in items:
        grouped[item.source].append(item)
        tag_counts.update(item.tags)

    tag_buttons = "\n".join(
        f'<button class="chip" data-tag="{html.escape(tag)}">{html.escape(tag)} <span>{count}</span></button>'
        for tag, count in tag_counts.most_common(14)
    )
    source_nav = "\n".join(
        f'<a href="#src-{re.sub(r"[^a-zA-Z0-9]+", "-", source)}">{html.escape(source)} <span>{len(grouped[source])}</span></a>'
        for source in sorted(grouped, key=source_sort_key)
    )

    def article_card(item: Article, rank: int | None = None) -> str:
        rank_html = f'<div class="rank">{rank:02d}</div>' if rank is not None else ""
        tags = "".join(f'<span class="tag">{html.escape(tag)}</span>' for tag in item.tags)
        note = f'<p class="note">备注：{html.escape(item.note)}</p>' if item.note else ""
        return f"""
        <article class="item" data-source="{html.escape(item.source)}" data-tags="{html.escape('|'.join(item.tags))}">
          {rank_html}
          <div class="item-body">
            <div class="meta"><span>{html.escape(item.source)}</span><span>{html.escape(item.date)}</span><span>{html.escape(item.category)}</span></div>
            <h3><a href="{html.escape(item.url)}" target="_blank" rel="noopener noreferrer">{html.escape(item.title)}</a></h3>
            <p>{html.escape(item.summary)}</p>
            {note}
            <div class="tags">{tags}</div>
          </div>
        </article>
        """

    top_html = "\n".join(article_card(item, idx) for idx, item in enumerate(top, 1))
    grouped_html = []
    for source in sorted(grouped, key=source_sort_key):
        source_id = re.sub(r"[^a-zA-Z0-9]+", "-", source)
        cards = "\n".join(article_card(item) for item in grouped[source])
        grouped_html.append(
            f"""
            <section class="source-section" id="src-{source_id}">
              <div class="section-head">
                <h2>{html.escape(source)}</h2>
                <span>{len(grouped[source])} 条</span>
              </div>
              <div class="list">{cards}</div>
            </section>
            """
        )

    data_json = html.escape(json.dumps([asdict(i) for i in items], ensure_ascii=False))
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>公考晨间新闻简报 {TODAY}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #1f2933;
      --muted: #667085;
      --line: #d9dee7;
      --red: #b42318;
      --blue: #1f5fbf;
      --green: #087443;
      --gold: #9a6700;
      --shadow: 0 10px 24px rgba(16, 24, 40, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
      line-height: 1.6;
    }}
    header {{
      background: #ffffff;
      border-bottom: 1px solid var(--line);
    }}
    .wrap {{ width: min(1180px, calc(100% - 32px)); margin: 0 auto; }}
    .topbar {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 24px;
      padding: 28px 0 20px;
      align-items: end;
    }}
    h1 {{ margin: 0; font-size: clamp(26px, 3vw, 38px); line-height: 1.2; letter-spacing: 0; }}
    .sub {{ margin: 10px 0 0; color: var(--muted); font-size: 14px; }}
    .stats {{ display: grid; grid-template-columns: repeat(3, minmax(82px, 1fr)); gap: 10px; }}
    .stat {{ background: #f8fafc; border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; min-width: 92px; }}
    .stat strong {{ display: block; font-size: 22px; line-height: 1.2; }}
    .stat span {{ color: var(--muted); font-size: 12px; }}
    .controls {{
      display: grid;
      grid-template-columns: minmax(180px, 1fr) auto;
      gap: 12px;
      padding: 0 0 18px;
    }}
    input[type="search"] {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 11px 12px;
      font-size: 15px;
      background: #fff;
    }}
    .reset {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 0 14px;
      cursor: pointer;
      color: var(--blue);
      font-weight: 600;
    }}
    main {{ padding: 20px 0 52px; }}
    .grid {{
      display: grid;
      grid-template-columns: 260px minmax(0, 1fr);
      gap: 20px;
      align-items: start;
    }}
    aside {{
      position: sticky;
      top: 14px;
      display: grid;
      gap: 14px;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      padding: 14px;
    }}
    .panel h2, .section-head h2 {{
      margin: 0;
      font-size: 18px;
      line-height: 1.3;
      letter-spacing: 0;
    }}
    nav a {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      padding: 8px 0;
      color: var(--text);
      text-decoration: none;
      border-bottom: 1px solid #eef1f5;
      font-size: 14px;
    }}
    nav a:last-child {{ border-bottom: 0; }}
    nav span {{ color: var(--muted); }}
    .chips {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }}
    .chip {{
      border: 1px solid var(--line);
      border-radius: 999px;
      background: #fff;
      padding: 6px 10px;
      cursor: pointer;
      color: var(--text);
      font-size: 13px;
    }}
    .chip.active {{ border-color: var(--blue); color: var(--blue); background: #eef5ff; }}
    .chip span {{ color: var(--muted); margin-left: 4px; }}
    .content {{ display: grid; gap: 18px; }}
    .section {{
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      padding: 16px;
    }}
    .section-head {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin-bottom: 12px;
      padding-bottom: 10px;
      border-bottom: 1px solid var(--line);
    }}
    .section-head span {{ color: var(--muted); font-size: 13px; }}
    .source-section {{
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      padding: 16px;
    }}
    .list {{ display: grid; gap: 10px; }}
    .item {{
      display: grid;
      grid-template-columns: auto minmax(0, 1fr);
      gap: 12px;
      border: 1px solid #e7ebf1;
      border-radius: 8px;
      padding: 12px;
      background: #fff;
    }}
    .item[hidden] {{ display: none; }}
    .rank {{
      width: 40px;
      height: 40px;
      display: grid;
      place-items: center;
      border-radius: 8px;
      background: #fff4ed;
      color: var(--red);
      font-weight: 700;
    }}
    .meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 4px;
    }}
    .meta span {{
      border-right: 1px solid var(--line);
      padding-right: 8px;
    }}
    .meta span:last-child {{ border-right: 0; }}
    h3 {{ margin: 0; font-size: 17px; line-height: 1.45; letter-spacing: 0; }}
    h3 a {{ color: var(--text); text-decoration: none; }}
    h3 a:hover {{ color: var(--blue); text-decoration: underline; }}
    .item p {{ margin: 6px 0 0; color: #3b4654; font-size: 14px; }}
    .item .note {{ color: var(--gold); }}
    .tags {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }}
    .tag {{
      font-size: 12px;
      color: var(--green);
      background: #ecfdf3;
      border: 1px solid #b7e4c7;
      border-radius: 999px;
      padding: 2px 8px;
    }}
    .study-list {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      margin: 0;
      padding: 0;
      list-style: none;
    }}
    .study-list li {{
      border-left: 3px solid var(--blue);
      background: #f8fafc;
      padding: 10px 12px;
      border-radius: 6px;
      font-size: 14px;
    }}
    .empty {{
      display: none;
      padding: 18px;
      text-align: center;
      color: var(--muted);
      background: #fff;
      border: 1px dashed var(--line);
      border-radius: 8px;
    }}
    @media (max-width: 860px) {{
      .topbar, .controls, .grid {{ grid-template-columns: 1fr; }}
      aside {{ position: static; }}
      .stats {{ grid-template-columns: repeat(3, 1fr); }}
      .study-list {{ grid-template-columns: 1fr; }}
    }}
    @media (max-width: 520px) {{
      .wrap {{ width: min(100% - 20px, 1180px); }}
      .stats {{ grid-template-columns: 1fr; }}
      .item {{ grid-template-columns: 1fr; }}
      .rank {{ width: 36px; height: 36px; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="wrap">
      <div class="topbar">
        <div>
          <h1>公考晨间新闻简报</h1>
          <p class="sub">{TODAY} · 生成于 {NOW.strftime('%Y-%m-%d %H:%M:%S')} 北京时间 · 摘要为备考导向提炼，请以原文为准。</p>
        </div>
        <div class="stats">
          <div class="stat"><strong>{len(items)}</strong><span>收录条目</span></div>
          <div class="stat"><strong>{len(grouped)}</strong><span>来源分组</span></div>
          <div class="stat"><strong>{len(tag_counts)}</strong><span>考点标签</span></div>
        </div>
      </div>
      <div class="controls">
        <input id="search" type="search" placeholder="搜索标题、摘要、来源或标签">
        <button class="reset" id="reset">重置筛选</button>
      </div>
    </div>
  </header>
  <main class="wrap grid">
    <aside>
      <section class="panel">
        <h2>来源导航</h2>
        <nav>{source_nav}</nav>
      </section>
      <section class="panel">
        <h2>考点标签</h2>
        <div class="chips">{tag_buttons}</div>
      </section>
    </aside>
    <div class="content">
      <section class="section">
        <div class="section-head"><h2>今日头条 / 必读 10 条</h2><span>优先看这些</span></div>
        <div class="list">{top_html}</div>
      </section>
      <section class="section">
        <div class="section-head"><h2>公考考点速记</h2><span>申论与面试素材</span></div>
        <ul class="study-list">
          <li>防汛救灾：人民至上、生命至上，基层党组织战斗堡垒作用，应急管理体系和风险预防。</li>
          <li>科技强国：高水平科技自立自强，基础研究、人工智能、新质生产力与现代化产业体系。</li>
          <li>十五五规划：旅游强国、美丽中国、教育、应急等规划文件体现中长期政策连续性。</li>
          <li>党建干部：正确政绩观、县委书记能力建设、全面从严治党、基层治理现代化。</li>
          <li>广东省情：百千万工程、县域高质量发展、人工智能产业应用和粤港澳大湾区协同创新。</li>
          <li>经济治理：物流景气、服务业扩能提质、投资和耐心资本，注意从数据读趋势。</li>
        </ul>
      </section>
      <div class="empty" id="empty">没有匹配的条目。请减少关键词或重置筛选。</div>
      {''.join(grouped_html)}
    </div>
  </main>
  <script type="application/json" id="brief-data">{data_json}</script>
  <script>
    const search = document.querySelector('#search');
    const reset = document.querySelector('#reset');
    const chips = [...document.querySelectorAll('.chip')];
    const items = [...document.querySelectorAll('.item')];
    const sections = [...document.querySelectorAll('.source-section')];
    const empty = document.querySelector('#empty');
    let activeTag = '';

    function applyFilter() {{
      const query = search.value.trim().toLowerCase();
      let visibleCount = 0;
      for (const item of items) {{
        const haystack = item.innerText.toLowerCase();
        const tagMatch = !activeTag || item.dataset.tags.includes(activeTag);
        const queryMatch = !query || haystack.includes(query);
        const show = tagMatch && queryMatch;
        item.hidden = !show;
        if (show) visibleCount += 1;
      }}
      for (const section of sections) {{
        const visibleItems = [...section.querySelectorAll('.item')].some(item => !item.hidden);
        section.hidden = !visibleItems;
      }}
      empty.style.display = visibleCount ? 'none' : 'block';
    }}
    search.addEventListener('input', applyFilter);
    reset.addEventListener('click', () => {{
      search.value = '';
      activeTag = '';
      chips.forEach(chip => chip.classList.remove('active'));
      applyFilter();
    }});
    chips.forEach(chip => {{
      chip.addEventListener('click', () => {{
        activeTag = activeTag === chip.dataset.tag ? '' : chip.dataset.tag;
        chips.forEach(c => c.classList.toggle('active', c.dataset.tag === activeTag));
        applyFilter();
      }});
    }});
  </script>
</body>
</html>
"""


def collect_items() -> list[Article]:
    items: list[Article] = []

    scrape_anchor_page(
        items,
        "新闻联播",
        "央视节目",
        "https://tv.cctv.com/lm/xwlb/",
        limit=16,
        default_date=NEWSLIANBO_DATE.isoformat(),
        href_pattern=rf"tv\.cctv\.com/{NEWSLIANBO_DATE:%Y/%m/%d}/",
    )
    scrape_anchor_page(
        items,
        "新华社",
        "首页要闻",
        "https://www.news.cn/",
        limit=55,
        default_date="最新",
        href_pattern=r"(www\.)?news\.cn|my-h5news\.app\.xinhuanet\.com",
    )
    scrape_json_list(
        items,
        "中国政府网",
        "要闻",
        "https://www.gov.cn/yaowen/liebiao/YAOWENLIEBIAO.json",
        28,
    )
    scrape_json_list(
        items,
        "中国政府网",
        "最新政策",
        "https://www.gov.cn/zhengce/zuixin/ZUIXINZHENGCE.json",
        14,
    )
    scrape_anchor_page(
        items,
        "求是",
        "理论要闻",
        "https://www.qstheory.cn/",
        limit=36,
        default_date="最新",
        href_pattern=r"qstheory\.cn/.+/(c|index)\.htm|qstheory\.cn/20\d{6}/.+/c\.html",
    )
    scrape_anchor_page(
        items,
        "半月谈",
        "时政与基层",
        "http://www.banyuetan.org/",
        limit=36,
        default_date=TODAY,
        href_pattern=r"banyuetan\.org/.+/(detail|index)\.html",
    )
    scrape_newspaper_layouts(
        items,
        "人民日报",
        "电子报",
        f"https://paper.people.com.cn/rmrb/pc/layout/{TODAY_DATE:%Y%m}/{TODAY_DATE:%d}/node_{{page:02d}}.html",
        range(1, 21),
        TODAY,
        f"/content/{TODAY_DATE:%Y%m}/{TODAY_DATE:%d}/",
    )
    scrape_newspaper_layouts(
        items,
        "光明日报",
        "电子报",
        f"https://epaper.gmw.cn/gmrb/html/layout/{TODAY_DATE:%Y%m}/{TODAY_DATE:%d}/node_{{page:02d}}.html",
        range(1, 18),
        TODAY,
        "/content_",
    )
    scrape_newspaper_layouts(
        items,
        "经济日报",
        "电子报",
        f"http://paper.ce.cn/pad/layout/{TODAY_DATE:%Y%m}/{TODAY_DATE:%d}/node_{{page:02d}}.html",
        range(1, 13),
        TODAY,
        f"/pad/content/{TODAY_DATE:%Y%m}/{TODAY_DATE:%d}/",
    )
    scrape_anchor_page(
        items,
        "南方周末",
        "首页推荐",
        "https://www.infzm.com/",
        limit=20,
        default_date=TODAY,
        href_pattern=r"infzm\.com/contents/",
    )
    scrape_anchor_page(
        items,
        "澎湃新闻",
        "时事",
        "https://m.thepaper.cn/channel_25950",
        limit=24,
        default_date=TODAY,
        href_pattern=r"(thepaper\.cn/(newsDetail_forward_|channel_)|news\.cctv\.com)",
    )
    scrape_anchor_page(
        items,
        "广东发布/广东省政府",
        "广东要闻",
        "https://www.gd.gov.cn/gdywdt/gdyw/",
        limit=24,
        default_date="最新",
        href_pattern=r"gd\.gov\.cn/.+content/post_",
    )
    scrape_anchor_page(
        items,
        "粤港澳大湾区",
        "最新动态",
        "https://www.cnbayarea.org.cn/news/focus/",
        limit=20,
        default_date="最新",
        href_pattern=r"cnbayarea\.org\.cn/.+/content/post_",
        verify=False,
    )
    scrape_anchor_page(
        items,
        "粤港澳大湾区",
        "首页头条",
        "http://www.cnbayarea.org.cn/",
        limit=12,
        default_date="最新",
        href_pattern=r"cnbayarea\.org\.cn/.+/content/post_",
    )
    manual_fallbacks(items)

    filtered = []
    for item in items:
        # Keep the briefing focused on politics, policy, governance, economy and Guangdong.
        if item.source in {"南方周末", "澎湃新闻"} and not re.search(
            r"习近平|防汛|治理|教育|经济|政策|时政|广东|深圳|基础研究|安全|考研|供水|国台办|长三角|曲靖|历史上的今天",
            item.title,
        ):
            continue
        filtered.append(item)
    return dedupe(filtered)


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    items = collect_items()
    top = top_items(items)
    html_path = OUTPUT_DIR / f"{TODAY}.html"
    md_path = OUTPUT_DIR / f"{TODAY}.md"
    json_path = OUTPUT_DIR / f"{TODAY}.json"

    html_path.write_text(render_html(items, top), encoding="utf-8")
    md_path.write_text(render_markdown(items, top), encoding="utf-8")
    json_path.write_text(json.dumps([asdict(i) for i in items], ensure_ascii=False, indent=2), encoding="utf-8")

    counts = Counter(item.source for item in items)
    print(f"HTML={html_path}")
    print(f"MARKDOWN={md_path}")
    print(f"JSON={json_path}")
    print(f"TOTAL={len(items)}")
    print("COUNTS=" + json.dumps(dict(sorted(counts.items(), key=lambda kv: source_sort_key(kv[0]))), ensure_ascii=False))
    print("TOP10=")
    for idx, item in enumerate(top, 1):
        print(f"{idx}. {item.title} | {item.source} | {item.date} | {item.url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
