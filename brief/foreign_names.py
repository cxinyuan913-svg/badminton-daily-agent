"""外國選手譯名表（交接單 003 第 1 部分）→ config/players_zh_foreign.csv

譯名來源**只用台灣媒體原文**（中央社、NOWnews），不用 LLM、不用拼音自己造：
  - 從文章找「中文名（英文名）」並列的寫法，英文名對回資料庫裡的 BWF 外國選手（姓名每個字都要對上、且唯一）
  - 每筆都附原文句子＋網址
  - 同一譯名 ≥ 2 個來源網站 → confirmed，否則 candidate；兩個網站譯名不同時以中央社為準，另一個記進證據
  - confirmed / rejected 由 Raymond 改過的不會被程式蓋掉
日報與腳本：外國選手有 confirmed 譯名時寫「坤拉武特（泰國）」，沒有就用英文。

用法：
  python -m brief.foreign_names fetch --db data/brief.db URL ...   # 抓指定的台灣媒體文章並擷取
  python -m brief.foreign_names export --db data/brief.db          # 匯出 config/players_zh_foreign.csv
"""
from __future__ import annotations

import argparse
import csv
import html
import re
from pathlib import Path

from brief.crawler import Client, connect

CSV_PATH = Path(__file__).resolve().parent.parent / "config" / "players_zh_foreign.csv"
SOURCES = {"www.cna.com.tw": "cna", "www.nownews.com": "nownews"}
PRIORITY = ["cna", "nownews"]                          # 譯名衝突時以中央社為準

TABLE = """
CREATE TABLE IF NOT EXISTS foreign_article (
    url             TEXT PRIMARY KEY,
    source          TEXT NOT NULL,
    text            TEXT NOT NULL,
    fetched_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS foreign_name_seen (
    player_id       INTEGER NOT NULL,
    name_zh         TEXT NOT NULL,
    source          TEXT NOT NULL,
    url             TEXT NOT NULL,
    evidence        TEXT NOT NULL,
    PRIMARY KEY (player_id, name_zh, url)
);
CREATE TABLE IF NOT EXISTS foreign_name (
    player_id       INTEGER PRIMARY KEY,
    name_en         TEXT,
    name_zh         TEXT NOT NULL,
    country         TEXT,
    source          TEXT,
    status          TEXT NOT NULL DEFAULT 'candidate',   -- candidate / confirmed / rejected
    evidence        TEXT,
    added_at        TEXT NOT NULL DEFAULT (date('now'))
);
"""

PAIR = re.compile(r"([一-鿿][一-鿿‧·．・]{1,9})[（(]\s*([A-Za-z][A-Za-z .'\-]{2,40}?)\s*[）)]")
TAG = re.compile(r"<[^>]+>")
SENT = re.compile(r"[^。！？\n]*")
# 「泰國好手坤拉武特」這類前綴不是名字：只保留緊貼英文括號的最後 2–4 個字以內的人名部分由呼叫端判斷
LEADING_WORDS = re.compile(r"^(?:.*?(?:好手|選手|名將|球后|球王|一哥|一姐|小將|老將|搭檔|組合|對手|女將|男將|新星|強敵|世界球后|世界球王))")


def page_text(page: str) -> str:
    """文章 HTML → 純文字段落（只取 <p>，避開選單與相關新聞）。"""
    paras = re.findall(r"<p[^>]*>(.*?)</p>", page, re.S)
    return "\n".join(html.unescape(TAG.sub("", p)).strip() for p in paras if p.strip())


def _tokens(name: str) -> list[str]:
    return sorted(t for t in re.split(r"[\s\-.']+", name.lower()) if t)


def match_player(con, name_en: str) -> int | None:
    """英文名對回資料庫裡唯一的非台灣選手：姓名每個字都要對上（不分大小寫、順序）。"""
    want = _tokens(name_en)
    if len(want) < 2:
        return None
    hits = [pid for pid, disp in con.execute(
        "SELECT player_id, name_display FROM player WHERE name_display IS NOT NULL AND COALESCE(country_code, '') != 'TPE'")
        if _tokens(disp) == want]
    return hits[0] if len(hits) == 1 else None


PARTICLE = re.compile(r"迎戰|擊敗|不敵|戰勝|力退|對上|對決|面對|對手|頭號|男雙|女雙|混雙|男單|女單|[的與和及遭被讓將]")


def _country_names() -> list[str]:
    from brief.zh import COUNTRY
    return sorted(set(COUNTRY.values()) | {"南韓", "大馬", "星國"}, key=len, reverse=True)


def clean_zh(name: str) -> str:
    """去掉「泰國好手」這類前綴，以及黏在前面的助詞（「的法漢」「與馬丁」「名的烏拜迪拉」）：
    在「的、與、和、及、對」切開取最後一段（外國選手的音譯幾乎不用這幾個字）。"""
    name = LEADING_WORDS.sub("", name) or name
    for c in _country_names():                 # 「韓國姜敏赫」→ 從國名之後開始取
        i = name.rfind(c)
        if i >= 0 and i + len(c) < len(name):
            name = name[i + len(c):]
            break
    name = PARTICLE.split(name)[-1] or name
    if len(name) == 4 and name[0] == "由":       # 「韓國由沈有振」；「由」不能當切點（福島由紀）
        name = name[1:]
    return name


def extract(con, text: str, url: str, source: str) -> list[tuple[int, str, str]]:
    """回傳 [(player_id, 中文譯名, 證據句)]。"""
    out = []
    for sent in SENT.findall(text):
        for zh_raw, en in PAIR.findall(sent):
            pid = match_player(con, en)
            if pid is None:
                continue
            zh = clean_zh(zh_raw)
            if 2 <= len(zh) <= 8:
                out.append((pid, zh, sent.strip()[:200]))
    return out


# 華裔選手（交接單 003：可用新聞找漢字，但要有證據）：BWF 名字是漢語拼音，台灣媒體多半只寫漢字、不附英文。
# 新聞原文裡的漢字名轉成拼音後，與 BWF 名字的每個字完全一樣、只對得上一位選手，且文章也寫到他的國家，才建立連結。
# 譯名本身仍然來自新聞原文，不是用拼音造的。
PINYIN_COUNTRIES = {"CHN": ("中國",), "HKG": ("香港",), "MAC": ("澳門",), "SGP": ("新加坡", "星國"), "MAS": ("馬來西亞", "大馬")}
CJK_RUN = re.compile(r"[一-鿿]{2,}")
# 常見華人姓氏（繁體）：拼音比對的第一個字必須是姓，排除「因疫情」「裡已經」「相遇」這類一般詞
SURNAMES = set("王李張劉陳楊黃趙吳周徐孫馬朱胡郭何林高羅鄭梁謝宋唐許韓馮鄧曹彭曾蕭田董袁潘于余蔣蔡賈丁魏薛葉閻杜戴夏鍾汪"
               "任姜范方石姚譚廖鄒熊金陸郝孔白崔康毛邱秦江史顧侯邵孟龍萬段雷錢湯尹黎易常武喬賀賴龔文翁鮑祁戚駱翟")
SEPARATORS = set("與和及／、，,·‧ ")


def _pinyin_index(con) -> dict[str, list[tuple[int, str]]]:
    from pypinyin import lazy_pinyin  # noqa: F401 — 確認套件存在
    idx: dict[str, list[tuple[int, str]]] = {}
    for pid, disp, country in con.execute(
            f"SELECT player_id, name_display, country_code FROM player WHERE country_code IN "
            f"({','.join('?' * len(PINYIN_COUNTRIES))}) AND name_display IS NOT NULL", tuple(PINYIN_COUNTRIES)):
        toks = disp.lower().split()
        if 2 <= len(toks) <= 3 and all(t.isalpha() for t in toks):
            idx.setdefault(" ".join(toks), []).append((pid, country))
    return idx


def extract_pinyin(con, text: str, index: dict | None = None) -> list[tuple[int, str, str]]:
    """規則：第一個字是常見姓氏；不是台灣選手全名的一部分；兩個字的名字前後必須緊貼分隔字（與、和、／、、…）。"""
    from pypinyin import lazy_pinyin
    from brief.zh import load_player_table
    index = _pinyin_index(con) if index is None else index
    tpe = [r["name_zh"] for r in load_player_table()]
    out = []
    for sent in SENT.findall(text):
        for m in CJK_RUN.finditer(sent):
            run, start = m.group(0), m.start()
            for size in (3, 2):
                for i in range(len(run) - size + 1):
                    word = run[i:i + size]
                    if word[0] not in SURNAMES:
                        continue
                    if any(word in t and word != t for t in tpe):
                        continue                       # 「宏蔚」是葉宏蔚的一部分
                    if size == 2:
                        before = sent[start + i - 1] if start + i > 0 else ""
                        after = sent[start + i + 2] if start + i + 2 < len(sent) else ""
                        if before not in SEPARATORS and after not in SEPARATORS:
                            continue
                    hits = index.get(" ".join(lazy_pinyin(word)))
                    if not hits or len(hits) != 1:
                        continue
                    pid, country = hits[0]
                    if any(c in text for c in PINYIN_COUNTRIES[country]):
                        out.append((pid, word, sent.strip()[:200]))
    return list({(p, w): (p, w, e) for p, w, e in out}.values())


def store(con, items: list[tuple[int, str, str]], url: str, source: str) -> int:
    con.executescript(TABLE)
    n = 0
    for pid, zh, ev in items:
        n += con.execute("INSERT OR IGNORE INTO foreign_name_seen VALUES (?,?,?,?,?)", (pid, zh, source, url, ev)).rowcount
    con.commit()
    refresh(con)
    return n


def refresh(con) -> None:
    """依出現紀錄重算每位選手的譯名與狀態；confirmed／rejected（Raymond 確認過的）不動。"""
    con.executescript(TABLE)
    by_player: dict[int, dict[str, dict[str, tuple[str, str]]]] = {}
    for pid, zh, source, url, ev in con.execute("SELECT player_id, name_zh, source, url, evidence FROM foreign_name_seen"):
        by_player.setdefault(pid, {}).setdefault(zh, {}).setdefault(source, (url, ev))
    for pid, names in by_player.items():
        row = con.execute("SELECT source FROM foreign_name WHERE player_id=?", (pid,)).fetchone()
        if row and row[0] == "raymond":
            continue                                           # Raymond 手動確認或否決過的不動
        # 以中央社用的譯名為主；沒有中央社就取來源最多的
        def rank(item):
            zh, srcs = item
            return (min((PRIORITY.index(s) if s in PRIORITY else 9) for s in srcs), -len(srcs))
        zh, srcs = min(names.items(), key=rank)
        status = "confirmed" if len(srcs) >= 2 else "candidate"
        evidence = "；".join(f"[{s}] {ev} <{url}>" for s, (url, ev) in sorted(srcs.items()))
        others = [f"另見「{z}」（{'、'.join(sorted(v))}）" for z, v in names.items() if z != zh]
        if others:
            evidence += "；" + "；".join(others)
        name_en, country = con.execute("SELECT name_display, country_code FROM player WHERE player_id=?", (pid,)).fetchone()
        con.execute(
            """INSERT INTO foreign_name (player_id, name_en, name_zh, country, source, status, evidence) VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(player_id) DO UPDATE SET name_zh=excluded.name_zh, source=excluded.source,
                 status=excluded.status, evidence=excluded.evidence""",
            (pid, name_en, zh, country, "、".join(sorted(srcs)), status, evidence))
    con.commit()


def fetch(con, client: Client, urls: list[str]) -> dict[str, int]:
    out = {}
    for url in urls:
        host = re.sub(r"^https?://([^/]+)/.*$", r"\1", url)
        source = SOURCES.get(host)
        if source is None:
            raise ValueError(f"不在允許的台灣媒體清單：{url}")
        r = client.get(url)
        if r is None:
            out[url] = 0
            continue
        text = page_text(r.text)
        con.executescript(TABLE)
        con.execute("INSERT OR REPLACE INTO foreign_article (url, source, text) VALUES (?, ?, ?)", (url, source, text))
        out[url] = scan_text(con, text, url, source)
    return out


def scan_text(con, text: str, url: str, source: str) -> int:
    return store(con, extract(con, text, url, source) + extract_pinyin(con, text), url, source)


def rescan(con) -> int:
    """用已存的文章內文重新擷取（改規則後不用再抓一次）。"""
    con.executescript(TABLE)
    return sum(scan_text(con, t, u, s) for u, s, t in con.execute("SELECT url, source, text FROM foreign_article").fetchall())


def scan_new(con, client: Client, limit: int = 20) -> int:
    """每日流程：抓還沒看過的台灣媒體羽球新聞內文（每則一次），擷取譯名。"""
    con.executescript(TABLE)
    try:
        urls = [u for (u,) in con.execute(
            "SELECT url FROM news_item WHERE source IN ('cna', 'nownews') AND url NOT IN (SELECT url FROM foreign_article) "
            "ORDER BY published DESC LIMIT ?", (limit,))]
    except Exception:  # noqa: BLE001 — 還沒有 news_item
        return 0
    return sum(fetch(con, client, urls).values()) if urls else 0


def export(con, path: Path = CSV_PATH) -> int:
    con.executescript(TABLE)
    rows = con.execute("SELECT player_id, name_en, name_zh, country, source, status, evidence FROM foreign_name "
                       "ORDER BY status, country, name_en").fetchall()
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["bwf_player_id", "英文名", "中文譯名", "國家", "來源", "狀態", "證據"])
        w.writerows(rows)
    return len(rows)


def weekly_lines(con, today) -> list[str]:
    """週一暱稱週報後面的「本週新增譯名」：7 天內新增的譯名，每則一行＋證據。"""
    import datetime as dt
    if today.weekday() != 0:
        return []
    try:
        rows = con.execute("SELECT name_en, name_zh, country, status, evidence FROM foreign_name "
                           "WHERE added_at > ? ORDER BY status, name_en",
                           ((today - dt.timedelta(days=7)).isoformat(),)).fetchall()
    except Exception:  # noqa: BLE001
        return []
    if not rows:
        return []
    from brief.zh import country
    lines = ["", "__**本週新增譯名**__（來源：台灣媒體原文；回覆「確認／否決 譯名」即可）"]
    for en, zh_name, c, status, ev in rows:
        label = "已確認" if status == "confirmed" else "待確認"
        lines.append(f"- 【{label}】{zh_name}（{country(c)}）= {en}｜{(ev or '')[:120]}")
    return lines


def confirmed_names(con) -> dict[int, str]:
    try:
        return dict(con.execute("SELECT player_id, name_zh FROM foreign_name WHERE status='confirmed'"))
    except Exception:  # noqa: BLE001 — 還沒有譯名表
        return {}


def main():
    ap = argparse.ArgumentParser(description="外國選手譯名表")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--db", default="brief.db")
    f.add_argument("urls", nargs="+")
    e = sub.add_parser("export")
    e.add_argument("--db", default="brief.db")
    a = ap.parse_args()
    con = connect(a.db)
    if a.cmd == "fetch":
        for url, n in fetch(con, Client(), a.urls).items():
            print(n, url)
    print("匯出", export(con), "筆 →", CSV_PATH)


if __name__ == "__main__":
    main()
