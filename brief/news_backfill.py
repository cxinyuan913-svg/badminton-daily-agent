"""新聞兩年回補（notes 10-02 07:25 第 1 點，Raymond 選 A＋D）：可中斷續跑，進度存在資料庫。

  TSNA   https://tsna.com/sitemap/year/YYYY 列每篇文章網址（沒有標題）→ 每篇抓一次，用標題＋全文篩羽球（news.is_badminton）
  BWF    https://bwfbadminton.com/wp-json/internal-api/vue-all-news（新聞列表頁自己呼叫的分頁接口，回傳內文）往回翻

速率：每來源每 10 秒 1 個請求（≤ 6 個／分鐘，notes 06:25）。robots：TSNA 只擋 /search；bwfbadminton.com 只擋 live blog。
進度：BWF 記在 news_backfill（翻到第幾頁）；TSNA 每篇記在 news_seen（看過就不再抓）。通過篩選的存 news_item＋foreign_article。

  python -m brief.news_backfill --db data/brief.db --source bwf|tsna [--since 2024-10-02]
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import re
import sys
import time

from brief import news
from brief.crawler import HEADERS

GAP_SEC = 10.0
BWF_API = "https://bwfbadminton.com/wp-json/internal-api/vue-all-news"
TSNA_SITEMAP = "https://tsna.com/sitemap/year/{year}"
STATE_TABLE = """
CREATE TABLE IF NOT EXISTS news_backfill (
    source          TEXT PRIMARY KEY,
    cursor          TEXT,                         -- BWF：下一頁頁碼；TSNA：目前處理的年份
    done            INTEGER NOT NULL DEFAULT 0,
    fetched         INTEGER NOT NULL DEFAULT 0,   -- 累計請求數
    stored          INTEGER NOT NULL DEFAULT 0,   -- 累計存進 news_item 的羽球新聞
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""
TAG = re.compile(r"<[^>]+>")


def _text(fragment: str) -> str:
    fragment = re.sub(r"<br\s*/?>", "\n", fragment or "")
    return "\n".join(t.strip() for t in html.unescape(TAG.sub("", fragment)).splitlines() if t.strip())


def _state(con, source: str) -> dict:
    con.executescript(STATE_TABLE)
    con.execute("INSERT OR IGNORE INTO news_backfill (source) VALUES (?)", (source,))
    row = con.execute("SELECT cursor, done, fetched, stored FROM news_backfill WHERE source=?", (source,)).fetchone()
    return dict(zip(["cursor", "done", "fetched", "stored"], row))


def _save(con, source: str, **kw) -> None:
    sets = ", ".join(f"{k}=?" for k in kw) + ", updated_at=datetime('now')"
    con.execute(f"UPDATE news_backfill SET {sets} WHERE source=?", (*kw.values(), source))
    con.commit()


def _store(con, source: str, url: str, title: str, published: str, text: str) -> int:
    from brief import foreign_names
    con.executescript(foreign_names.TABLE)
    added = news.store(con, [{"url": url, "source": source, "title": title, "published": published}])
    con.execute("INSERT OR REPLACE INTO foreign_article (url, source, text) VALUES (?, ?, ?)", (url, source, text))
    return added


# ---------------------------------------------------------------- BWF
def bwf_item(d: dict) -> dict:
    date = (d.get("post_date") or "")[:10]
    y, m, dd = date.split("-") if date else ("", "", "")
    return {"url": f"https://bwfbadminton.com/news-single/{y}/{m}/{dd}/{d.get('post_name')}/", "published": date,
            "title": html.unescape(d.get("post_title") or ""), "text": _text(d.get("post_content") or "")}


def run_bwf(con, session, since: str, log=print) -> None:
    st = _state(con, "bwf")
    if st["done"]:
        log("bwf：已完成")
        return
    page = int(st["cursor"] or 1)
    fetched, stored = st["fetched"], st["stored"]
    while True:
        r = session.post(BWF_API, json={"page": page, "pageKey": 10, "siteUrl": "https://bwfbadminton.com", "locale": "en",
                                         "activeTab": 0, "drawCount": 1}, headers=HEADERS, timeout=60)
        fetched += 1
        res = r.json()["results"]
        items = [bwf_item(d) for d in res["data"]]
        for it in items:
            if it["published"] >= since:
                stored += _store(con, "bwf", it["url"], it["title"], it["published"], it["text"])
        oldest = min((it["published"] for it in items), default="")
        log(f"bwf 第 {page}/{res['last_page']} 頁，最舊 {oldest}，累計存 {stored} 則")
        page += 1
        done = not items or oldest < since or page > res["last_page"]
        _save(con, "bwf", cursor=str(page), fetched=fetched, stored=stored, done=int(done))
        if done:
            log("bwf：完成")
            return
        time.sleep(GAP_SEC)


# ---------------------------------------------------------------- TSNA
def tsna_urls(session, year: int, since: str, until: str = "9999-12-31") -> list[str]:
    t = session.get(TSNA_SITEMAP.format(year=year), headers=HEADERS, timeout=120).text
    pairs = re.findall(r"<loc>(https://tsna\.com/article/\d+)</loc>\s*<lastmod>([^<]+)</lastmod>", t)
    return [u.replace("https://tsna.com/", "https://www.tsna.com/") for u, mod in pairs if since <= mod[:10] <= until]


def tsna_parse(page: str) -> tuple[str, str, str]:
    title = re.search(r'<meta property="og:title" content="([^"]+)"', page)
    date = re.search(r'article:published_time" content="(\d{4}-\d{2}-\d{2})', page)
    body = re.search(r'<div id="centercontainer" class="userBlk">(.*?)</div>', page, re.S)
    return (html.unescape(title.group(1)) if title else "", date.group(1) if date else "", _text(body.group(1) if body else ""))


def run_tsna(con, session, since: str, log=print, until: str | None = None) -> None:
    """until：notes 16:55（Raymond 選 B）只補到 2024-12-31，2025 年先不補。"""
    st = _state(con, "tsna")
    if st["done"]:
        log("tsna：已完成")
        return
    names = news.player_names(con)
    fetched, stored = st["fetched"], st["stored"]
    until = until or dt.date.today().isoformat()
    years = range(int(since[:4]), int(until[:4]) + 1)
    for year in years:
        urls = tsna_urls(session, year, since, until)
        fetched += 1
        con.executescript(news.RUN_TABLE)
        seen = {u for (u,) in con.execute("SELECT url FROM news_seen WHERE source='tsna'")}
        todo = [u for u in urls if u not in seen]
        log(f"tsna {year}：sitemap {len(urls)} 篇，還沒看過 {len(todo)} 篇")
        _save(con, "tsna", cursor=str(year), fetched=fetched)
        for i, url in enumerate(todo, 1):
            time.sleep(GAP_SEC)
            try:
                r = session.get(url, headers=HEADERS, timeout=60)
                fetched += 1
            except Exception as e:  # noqa: BLE001 — 單篇失敗下次再試（沒記 news_seen）
                log(f"  失敗 {url}：{e!r}")
                continue
            if r.status_code != 200:
                continue
            title, date, text = tsna_parse(r.text)
            ok = news.is_badminton(title, text, names)
            con.execute("INSERT OR REPLACE INTO news_seen (url, source, title, badminton) VALUES (?,?,?,?)",
                        (url, "tsna", title, int(ok)))
            if ok:
                stored += _store(con, "tsna", url, title, date, text)
            con.commit()                                  # 每篇都 commit：長交易會鎖住資料庫，watch／晨報就寫不進去（2026-10-02）
            if i % 50 == 0:
                _save(con, "tsna", fetched=fetched, stored=stored)
                log(f"  {year} {i}/{len(todo)}，累計存 {stored} 則")
        _save(con, "tsna", fetched=fetched, stored=stored)
    _save(con, "tsna", done=1)
    log("tsna：完成")


def main():
    import requests
    from brief.crawler import connect
    ap = argparse.ArgumentParser(description="新聞兩年回補（可中斷續跑）")
    ap.add_argument("--db", default="data/brief.db")
    ap.add_argument("--source", choices=["bwf", "tsna"], required=True)
    ap.add_argument("--since", default=(dt.date.today() - dt.timedelta(days=730)).isoformat())
    ap.add_argument("--until", help="TSNA 補到哪天（含）；預設今天")
    a = ap.parse_args()
    con = connect(a.db)
    session = requests.Session()
    log = lambda m: print(f"{dt.datetime.now():%m-%d %H:%M:%S} {m}", flush=True)
    log(f"開始 {a.source}，since {a.since}")
    try:
        if a.source == "bwf":
            run_bwf(con, session, a.since, log)
        else:
            run_tsna(con, session, a.since, log, a.until)
    except KeyboardInterrupt:
        log("中斷（進度已存，重跑會接著做）")
        sys.exit(1)


if __name__ == "__main__":
    main()
