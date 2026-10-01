"""羽球日報 Agent：新聞列表收集（P1，只存標題、網址、日期；內文與向量化在 P2）

來源（2026-09-30 實測，皆遵守 robots.txt）：
  - BWF 主站     https://bwfbadminton.com/news/                     <a title> + 網址裡的日期
  - BWF World Tour https://bwfworldtour.bwfbadminton.com/news/      同上；標題沒有 title 屬性時用圖片 alt
  - 中央社體育   https://www.cna.com.tw/list/aspt.aspx               網址 ID 前 8 碼是日期；robots 標示 ai-input=yes
  - NOWnews 運動 https://www.nownews.com/cat/sport/                  aria-label + <time datetime>
  - ETtoday 運動雲 https://sports.ettoday.net/news-list/新聞/最新新聞  <h3><a title> + <span class="date">（2026-10-01 加）
  - 公視新聞 即時 https://news.pts.org.tw/dailynews                   <h2 title> + <time datetime>（沒有體育分類，2026-10-01 加）
  - TSNA 台灣運動好事 https://www.tsna.com/all                        <a title> + <span class="time">（2026-10-01 加）
台灣媒體列表混了所有運動，用關鍵字篩出羽球（各站的「羽球分類頁」不存在或不準，見 docs/data-sources.md）。
收集頻率：brief.watch 每 30 分鐘一次（notes 16:00；列表只放最新幾十則，一天一次會漏）。

不用的來源：聯合新聞網、自由時報體育、Yahoo 奇摩運動、運動視界（robots.txt 禁止 ClaudeBot 等 AI 爬蟲）、
NOWnews 標籤頁（403）、Google 新聞 RSS（robots.txt 擋下）、中華民國羽球協會最新消息（停在 2024 年）。

新聞只當資訊來源：草稿要改寫並附出處，不可整段轉貼。

用法：python -m brief.news --db data/brief.db
"""
from __future__ import annotations

import argparse
import html
import re

from brief.crawler import Client, connect

SOURCES = {
    "bwf": "https://bwfbadminton.com/news/",
    "bwfworldtour": "https://bwfworldtour.bwfbadminton.com/news/",
    "cna": "https://www.cna.com.tw/list/aspt.aspx",
    "nownews": "https://www.nownews.com/cat/sport/",
    "ettoday": "https://sports.ettoday.net/news-list/%E6%96%B0%E8%81%9E/%E6%9C%80%E6%96%B0%E6%96%B0%E8%81%9E",
    "pts": "https://news.pts.org.tw/dailynews",
    "tsna": "https://www.tsna.com/all",
}
TAIWAN = ("cna", "nownews", "ettoday", "pts", "tsna")
# 台灣媒體的羽球篩選：基本詞 + 追蹤中的選手 + 退休但新聞仍以羽球為主的選手 + 已採用的暱稱
BASE_KEYWORDS = ["羽球", "羽毛球", "羽賽", "BWF", "湯姆斯盃", "尤伯盃", "蘇迪曼盃", "湯盃", "尤盃"]
# 正式名單 config/players_zh.csv 出現前的暫用選手（2026-09-30）；李洋已退休、現任運動部部長，新聞多為政策，不列
FALLBACK_PLAYERS = ["周天成", "王齊麟", "林俊易", "王子維", "李佳馨"]
# 退休、名單追蹤設 N，但新聞仍多與羽球有關，保留為關鍵字（2026-09-30 Raymond）
KEEP_AFTER_RETIRE = ["戴資穎"]


def keywords(con=None) -> list[str]:
    from brief import zh
    tracked = [r["name_zh"] for r in zh.load_player_table() if r.get("track", "Y") == "Y" and r["name_zh"]]
    players = tracked if zh.official_table_exists() else sorted(set(tracked) | set(FALLBACK_PLAYERS))
    words = BASE_KEYWORDS + players + KEEP_AFTER_RETIRE
    if con is not None:
        try:
            words += [r[0] for r in con.execute("SELECT nickname FROM nickname WHERE status IN ('auto', 'confirmed')")]
        except Exception:  # noqa: BLE001 — 還沒有 nickname 表
            pass
    return sorted(set(words))


def keyword_pattern(words: list[str]) -> re.Pattern:
    return re.compile("|".join(re.escape(w) for w in sorted(set(words), key=len, reverse=True)))


BADMINTON = keyword_pattern(keywords())

NEWS_TABLE = """
CREATE TABLE IF NOT EXISTS news_item (
    url             TEXT PRIMARY KEY,
    source          TEXT NOT NULL,
    title           TEXT NOT NULL,
    published       TEXT,                         -- YYYY-MM-DD 或 YYYY-MM-DD HH:MM（當地時間）
    fetched_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS digest_news (
    url             TEXT PRIMARY KEY,
    digest_date     TEXT NOT NULL
);
"""

BWF_LINK = re.compile(r'<a href="(https://[a-z.]*bwfbadminton\.com/news-single/(\d{4})/(\d{2})/(\d{2})/[^"]+)"([^>]*)>')
TITLE_ATTR = re.compile(r'title="([^"]+)"')
ALT_ATTR = re.compile(r'alt="([^"]+)"')
CNA_ITEM = re.compile(r'href="(/news/aspt/(\d{8})\d{4}\.aspx)"[^>]*>.*?<h2[^>]*>(?:<span>)?([^<]+)', re.S)
NOW_ITEM = re.compile(r'<a href="(https://www\.nownews\.com/news/\d+)"[^>]*aria-label="([^"]+)"(.*?)</a>', re.S)
NOW_TIME = re.compile(r'<time datetime="([\d-]+ [\d:]+)"')


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def parse_bwf(page: str, source: str) -> list[dict]:
    """同一篇會出現在圖片與標題兩個連結，以網址去重。標題優先用 <a title>，其次附近的 alt / title。"""
    out: dict[str, dict] = {}
    for m in BWF_LINK.finditer(page):
        url, y, mo, d, attrs = m.groups()
        title = TITLE_ATTR.search(attrs)
        if not title:
            nearby = page[m.end():m.end() + 600]
            title = ALT_ATTR.search(nearby) or TITLE_ATTR.search(nearby)
        title_text = _clean(title.group(1)) if title else ""
        if url in out and (out[url]["title"] or not title_text):
            continue
        out[url] = {"url": url, "source": source, "title": title_text, "published": f"{y}-{mo}-{d}"}
    return [r for r in out.values() if r["title"] and r["title"] != "BWF News"]


def parse_cna(page: str) -> list[dict]:
    out = {}
    for path, ymd, title in CNA_ITEM.findall(page):
        url = f"https://www.cna.com.tw{path}"
        out.setdefault(url, {"url": url, "source": "cna", "title": _clean(title),
                             "published": f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"})
    return list(out.values())


def parse_nownews(page: str) -> list[dict]:
    out = {}
    for url, title, body in NOW_ITEM.findall(page):
        t = NOW_TIME.search(body)
        if url not in out or (t and not out[url]["published"]):
            out[url] = {"url": url, "source": "nownews", "title": _clean(title),
                        "published": t.group(1) if t else None}
    return list(out.values())


def _items(page: str, anchor: re.Pattern, when: re.Pattern) -> list[tuple[tuple, tuple | None]]:
    """把列表頁切成一則一則（從這個標題到下一個標題），日期只在同一則裡找，避免抓到下一則的日期。"""
    hits = list(anchor.finditer(page))
    out = []
    for k, m in enumerate(hits):
        end = hits[k + 1].start() if k + 1 < len(hits) else len(page)
        w = when.search(page, m.end(), end)
        out.append((m.groups(), w.groups() if w else None))
    return out


ET_ITEM = re.compile(r'<h3><a href="(https://sports\.ettoday\.net/news/\d+)"[^>]*title="([^"]+)"')
ET_TIME = re.compile(r'<span class="date">(\d{4}-\d{2}-\d{2})')
PTS_ITEM = re.compile(r'<h2 title="([^"]+)">\s*<a\s+href="(https://news\.pts\.org\.tw/article/\d+)"')
PTS_TIME = re.compile(r'<time datetime="(\d{4}-\d{2}-\d{2})')
TSNA_ITEM = re.compile(r'<a href="(/article/\d+)" title="([^"]+)" class="card-info">')
TSNA_TIME = re.compile(r'<span class="time">(\d{4})年(\d{2})月(\d{2})日')


def parse_ettoday(page: str) -> list[dict]:
    out = {}
    for (url, title), w in _items(page, ET_ITEM, ET_TIME):
        out.setdefault(url, {"url": url, "source": "ettoday", "title": _clean(title), "published": w[0] if w else None})
    return list(out.values())


def parse_pts(page: str) -> list[dict]:
    out = {}
    for (title, url), w in _items(page, PTS_ITEM, PTS_TIME):
        out.setdefault(url, {"url": url, "source": "pts", "title": _clean(title), "published": w[0] if w else None})
    return list(out.values())


def parse_tsna(page: str) -> list[dict]:
    out = {}
    for (path, title), w in _items(page, TSNA_ITEM, TSNA_TIME):
        url = f"https://www.tsna.com{path}"
        out.setdefault(url, {"url": url, "source": "tsna", "title": _clean(title),
                             "published": "-".join(w) if w else None})
    return list(out.values())


TAIWAN_PARSERS = {"cna": parse_cna, "nownews": parse_nownews, "ettoday": parse_ettoday, "pts": parse_pts, "tsna": parse_tsna}


def parse(source: str, page: str, pattern: re.Pattern | None = None) -> list[dict]:
    if source in ("bwf", "bwfworldtour"):
        return parse_bwf(page, source)
    rows = TAIWAN_PARSERS[source](page)
    return [r for r in rows if (pattern or BADMINTON).search(r["title"])]


def store(con, rows: list[dict]) -> int:
    con.executescript(NEWS_TABLE)
    before = con.execute("SELECT COUNT(*) FROM news_item").fetchone()[0]
    for r in rows:
        con.execute(
            """INSERT INTO news_item (url, source, title, published) VALUES (:url, :source, :title, :published)
               ON CONFLICT(url) DO UPDATE SET title=excluded.title,
                 published=COALESCE(excluded.published, news_item.published)""", r)
    con.commit()
    return con.execute("SELECT COUNT(*) FROM news_item").fetchone()[0] - before


RUN_TABLE = """
CREATE TABLE IF NOT EXISTS news_run (
    run_at          TEXT NOT NULL DEFAULT (datetime('now')),
    source          TEXT NOT NULL,
    parsed          INTEGER,                      -- 列表頁解析出幾則（不管是不是羽球）
    matched         INTEGER,                      -- 符合關鍵字幾則
    added           INTEGER,                      -- 新寫入幾則
    error           TEXT
);
"""
ZERO_ALERT_HOURS = 24                             # 某來源連續 24 小時整頁解析 0 則 → 告警（notes 06:20 第 2 點）
ANY = re.compile(".")


def collect(client: Client, con, sources=SOURCES) -> tuple[int, list[str]]:
    """回傳（新增筆數, 錯誤）。單一來源失敗不影響其他來源。每次每個來源記一筆 news_run（解析幾則、符合幾則、新增幾則、錯誤）。"""
    con.executescript(RUN_TABLE)
    added, errors = 0, []
    pattern = keyword_pattern(keywords(con))
    for source, url in sources.items():
        parsed = matched = new = None
        err = None
        try:
            r = client.get(url)
            if r is None:
                raise ValueError("404")
            rows = parse(source, r.text, pattern)
            parsed = len(rows) if source in ("bwf", "bwfworldtour") else len(parse(source, r.text, ANY))
            matched = len(rows)
            new = store(con, rows)
            added += new
        except Exception as e:  # noqa: BLE001
            err = repr(e)
            errors.append(f"news {source}: {e!r}")
        con.execute("INSERT INTO news_run (source, parsed, matched, added, error) VALUES (?,?,?,?,?)",
                    (source, parsed, matched, new, err))
    con.commit()
    return added, errors


def zero_sources(con, hours: int = ZERO_ALERT_HOURS) -> list[str]:
    """過去 hours 小時每一次都整頁解析 0 則（或出錯）的來源；至少要有 3 次紀錄才算，避免剛上線就告警。"""
    con.executescript(RUN_TABLE)
    rows = con.execute("""SELECT source, COUNT(*), MAX(COALESCE(parsed, 0)) FROM news_run
                          WHERE run_at >= datetime('now', ?) GROUP BY source""", (f"-{hours} hours",)).fetchall()
    return [src for src, n, best in rows if n >= 3 and best == 0]


def main():
    ap = argparse.ArgumentParser(description="新聞列表收集")
    ap.add_argument("--db", default="brief.db")
    a = ap.parse_args()
    added, errors = collect(Client(), connect(a.db))
    print(f"新增 {added} 則新聞")
    for e in errors:
        print("錯誤：", e)


if __name__ == "__main__":
    main()
