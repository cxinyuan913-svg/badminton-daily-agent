"""羽球日報 Agent：BWF 賽事 ID 掃描器

BWF 官網行事曆只列 Grade 1–2，但 Grade 3（International Challenge / Series）
的賽事頁也在 https://bwfbadminton.com/tournament/{id}/ 底下。
這支程式逐一檢查賽事 ID，記錄名稱，再用頒獎台 API 的冠軍積分判斷層級。

層級判斷（2018 年新積分制之後，冠軍積分）：
  International Challenge 4000、International Series 2500、Future Series 1700
Grade 1–2 用名稱與 World Tour 賽程另外標記，不靠積分。

用法：
  python -m brief.scanner scan --from 2400 --to 5900      # 第一次全掃（約 1.5–2 小時）
  python -m brief.scanner scan --from 5760                # 之後只掃新 ID
  python -m brief.scanner classify                         # 判斷 Grade 3 層級
  python -m brief.scanner export grade3.csv                # 匯出 IC / IS 清單
"""
from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import time

from brief.crawler import API, SITE, Client

POINTS_TO_LEVEL = {4000: "IC", 2500: "IS", 1700: "FS"}
# 名稱明顯不是成人國際個人賽的，直接排除，不必查積分
EXCLUDE = re.compile(r"junior|\bU1\d\b|\bU2\d\b|under ?\d|youth|para|team|senior|masters games|"
                     r"university|school|club|league|national championships", re.I)
GRADE3_HINT = re.compile(r"international|challenge|series|open", re.I)

DDL = """
CREATE TABLE IF NOT EXISTS scan (
    tournament_id INTEGER PRIMARY KEY,
    http_status   INTEGER,
    name          TEXT,
    year          INTEGER,
    level         TEXT,
    winner_points REAL,
    scanned_at    TEXT DEFAULT (datetime('now'))
);
"""


def read_title(client: Client, tid: int):
    r = client.get(f"{SITE}/tournament/{tid}/x/")
    if r is None:
        return 404, None
    m = re.search(r"<title>([^<]*)", r.text)
    name = m.group(1).split("|", 1)[-1].strip() if m else ""
    return 200, name


def scan(con, client: Client, start: int, end: int, skip_done=True):
    done = {r[0] for r in con.execute("SELECT tournament_id FROM scan")} if skip_done else set()
    misses = 0
    for tid in range(start, end + 1):
        if tid in done:
            continue
        status, name = read_title(client, tid)
        year = re.search(r"(20\d{2})", name or "")
        con.execute("INSERT OR REPLACE INTO scan (tournament_id, http_status, name, year) VALUES (?,?,?,?)",
                    (tid, status, name, int(year.group(1)) if year else None))
        con.commit()
        print(tid, status, name or "")
        misses = misses + 1 if status == 404 else 0
        if end == 10**9 and misses >= 30:   # 掃新 ID 時，連續 30 個不存在就停
            break


def winner_points(client: Client, tid: int):
    """取頒獎台第一個項目的冠軍積分。"""
    r = client.get(f"{API}/vue-tournament-podium", drawCount=1, searchKey="", tmtTab="podium",
                   tmtId=tid, tmtType=0, podiumEventCode=1, isPara="false")
    if r is None:
        return None
    for ev in r.json().get("results", []):
        for w in ev.get("winners", []):
            if str(w.get("position")) == "1" and w.get("points"):
                return float(w["points"])
    return None


def classify(con, client: Client):
    rows = con.execute("SELECT tournament_id, name FROM scan WHERE http_status=200 AND level IS NULL").fetchall()
    for tid, name in rows:
        if EXCLUDE.search(name or "") or not GRADE3_HINT.search(name or ""):
            con.execute("UPDATE scan SET level='OTHER' WHERE tournament_id=?", (tid,))
            continue
        pts = winner_points(client, tid)
        level = POINTS_TO_LEVEL.get(int(pts)) if pts else None
        con.execute("UPDATE scan SET level=?, winner_points=? WHERE tournament_id=?",
                    (level or ("UNKNOWN" if pts is None else "OTHER"), pts, tid))
        con.commit()
        print(tid, name, pts, level)


def export(con, path: str):
    rows = con.execute("SELECT tournament_id, year, level, name FROM scan WHERE level IN ('IC','IS') "
                       "ORDER BY year, tournament_id").fetchall()
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["tournament_id", "year", "level", "name"])
        w.writerows(rows)
    print(f"匯出 {len(rows)} 站 → {path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["scan", "classify", "export"])
    ap.add_argument("out", nargs="?", default="grade3.csv")
    ap.add_argument("--from", dest="start", type=int, default=2400)
    ap.add_argument("--to", dest="end", type=int, default=10**9)
    ap.add_argument("--db", default="brief.db")
    ap.add_argument("--gap", type=float, default=2.0, help="每次請求間隔秒數")
    a = ap.parse_args()
    con = sqlite3.connect(a.db)
    con.executescript(DDL)
    if a.cmd == "export":
        return export(con, a.out)
    client = Client(gap=a.gap)
    if a.cmd == "scan":
        scan(con, client, a.start, a.end)
    else:
        classify(con, client)


if __name__ == "__main__":
    main()
