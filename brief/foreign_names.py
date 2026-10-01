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


PARTICLE = re.compile(r"迎戰|擊敗|不敵|戰勝|力退|對上|對決|面對|對手|[的與和及遭由被讓將]")


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
    return PARTICLE.split(name)[-1] or name


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
        out[url] = store(con, extract(con, page_text(r.text), url, source), url, source)
    return out


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
