"""羽球日報 Agent：新聞列表收集（P1，只存標題、網址、日期；內文與向量化在 P2）

來源（2026-09-30 實測，皆遵守 robots.txt）：
  - BWF 主站     https://bwfbadminton.com/news/                     <a title> + 網址裡的日期
  - BWF World Tour https://bwfworldtour.bwfbadminton.com/news/      同上；標題沒有 title 屬性時用圖片 alt
  - 中央社體育   https://www.cna.com.tw/list/aspt.aspx               網址 ID 前 8 碼是日期；robots 標示 ai-input=yes
  - NOWnews 運動 https://www.nownews.com/cat/sport/                  aria-label + <time datetime>
台灣媒體列表混了所有運動，用關鍵字篩出羽球。

不用的來源：聯合新聞網（robots.txt 禁止 Claude / ClaudeBot / GPTBot）、
NOWnews 標籤頁（403）、Google 新聞 RSS（robots.txt 擋下）。

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
}
# 台灣媒體的羽球篩選。選手名單請 Raymond 補充
BADMINTON = re.compile(r"羽球|羽毛球|羽賽|BWF|湯姆斯盃|尤伯盃|蘇迪曼盃|湯盃|尤盃|"
                       r"戴資穎|周天成|王齊麟|李洋|林俊易|王子維|李佳馨")

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


def parse(source: str, page: str) -> list[dict]:
    if source in ("bwf", "bwfworldtour"):
        return parse_bwf(page, source)
    rows = parse_cna(page) if source == "cna" else parse_nownews(page)
    return [r for r in rows if BADMINTON.search(r["title"])]


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


def collect(client: Client, con, sources=SOURCES) -> tuple[int, list[str]]:
    """回傳（新增筆數, 錯誤）。單一來源失敗不影響其他來源。"""
    added, errors = 0, []
    for source, url in sources.items():
        try:
            r = client.get(url)
            if r is None:
                raise ValueError("404")
            added += store(con, parse(source, r.text))
        except Exception as e:  # noqa: BLE001
            errors.append(f"news {source}: {e!r}")
    return added, errors


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
