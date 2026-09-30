"""羽球日報 Agent：BWF 年度賽程 → 追蹤範圍內的賽事清單

資料來源（2026-09-30 實測）：
  https://extranet-lv.bwfbadminton.com/api/vue-grouped-year-tournaments?year=YYYY
  **不要加 category[] 參數**（篩選結果不可靠）。不加參數時回傳該年全部賽事，
  每筆都有 category 名稱、GUID（code）、起訖日期、狀態。一年一個請求。

驗證：與頒獎台冠軍積分交叉比對 325 站，層級 100% 一致（4000=IC、2500=IS）。
這是找賽事與判斷層級的主要方法；scanner.py（ID 掃描）只當備援。

用法：
  python -m brief.calendar --from 2016 --to 2026 --db data/brief.db   # 寫入 tournament 表
  python -m brief.calendar --from 2016 --to 2026 --csv data/tournaments.csv
"""
from __future__ import annotations

import argparse
import csv
import re

from brief.crawler import API, Client, connect, upsert_tournament

# BWF 分類名稱 → (grade, level)。只列追蹤範圍內的成人國際個人／團體賽
CATEGORY_LEVEL = {
    "Grade 1 – Individual Tournaments": (1, "G1_IND"),       # 奧運、世錦賽
    "Grade 1 – Team Tournaments": (1, "G1_TEAM"),            # 湯尤盃、蘇迪曼盃
    "BWF Events": (1, "G1_EVENT"),                           # 2016–2017 舊分類，需搭配名稱
    "HSBC BWF World Tour Finals": (2, "WTF"),
    "HSBC BWF World Tour Super 1000": (2, "S1000"),
    "HSBC BWF World Tour Super 750": (2, "S750"),
    "HSBC BWF World Tour Super 500": (2, "S500"),
    "HSBC BWF World Tour Super 300": (2, "S300"),
    "BWF Tour Super 100": (2, "S100"),
    "World Superseries Premier": (2, "SSP"),                 # 2017 年以前的舊制
    "World Superseries": (2, "SS"),
    "Grand Prix Gold": (2, "GPG"),
    "Grand Prix": (2, "GP"),
    "International Challenge": (3, "IC"),
    "International Series": (3, "IS"),
    "Future Series": (3, "FS"),                              # 只存不推，用於排名重建（2026-09-30 22:35 決議）
    "Continental Team Championships": (None, "CONT_TEAM"),   # 洲際團體錦標賽，規章 7.1 計入個人排名（同上）
    "Continental Individual Championships": (3, "CONT_IND"),  # 亞錦賽、歐錦賽等（2026-09-30 決議納入）
    "Multi-Sport Games": (None, "MULTI"),                     # 亞運、大英國協運動會個人賽（同上）
    "Multi-Sport Games - Team Tournaments": (None, "MULTI_TEAM"),
}
# 早期的綜合運動會被歸在其他分類（例如 2018 亞運是 "Other"），用名稱補抓
MULTI_NAME = re.compile(r"asian games|commonwealth games", re.I)
MULTI_FALLBACK_CATEGORIES = {"Other", "Continental Team Games"}
# 世大運：V6.0 §7.1 個人與團體賽都計入世界排名（2026-09-30 22:45 決議納入、推送）。分類不固定，用名稱辨認
FISU_NAME = re.compile(r"FISU|world university games|universiade", re.I)
FISU_CATEGORIES = {"Multi-Sport Games", "Multi-Sport Games - Team Tournaments", "Grade 1 – Team Tournaments",
                   "Grade 1 – Individual Tournaments", "Other"}
EXCLUDE_NAME = re.compile(r"junior|senior|university|youth|\bpara\b|\bU1\d\b|\bU2\d\b", re.I)
G1_EVENT_NAME = re.compile(r"world championships|sudirman|thomas|uber|olympic|superseries finals", re.I)


def classify(t: dict):
    cat = re.sub(r"\s+", " ", t.get("category") or "").strip()
    name = t.get("name") or ""
    if FISU_NAME.search(name) and cat in FISU_CATEGORIES and not re.search(r"junior|youth", name, re.I):
        grade, level = None, "FISU"
    elif EXCLUDE_NAME.search(name):
        return None
    elif cat in MULTI_FALLBACK_CATEGORIES and MULTI_NAME.search(name):
        grade, level = None, ("MULTI_TEAM" if re.search(r"team", name, re.I) else "MULTI")
    elif cat not in CATEGORY_LEVEL:
        return None
    elif cat == "BWF Events" and not G1_EVENT_NAME.search(name):
        return None
    else:
        grade, level = CATEGORY_LEVEL[cat]
    return {
        "tournament_id": int(t["id"]),
        "code": (t.get("code") or "").upper() or None,
        "name": name,
        "grade": grade,
        "level": level,
        "category": cat,
        "start_date": (t.get("start_date") or "")[:10] or None,
        "end_date": (t.get("end_date") or "")[:10] or None,
        "country": t.get("country"),
        "status": (t.get("status") or {}).get("code"),
        "source_url": t.get("url"),
    }


def parse_year(payload: dict) -> list[dict]:
    out = []
    for month in payload.get("results", []):
        for t in month.get("tournaments", []):
            row = classify(t)
            if row:
                out.append(row)
    return out


def fetch_year(client: Client, year: int) -> list[dict]:
    r = client.get(f"{API}/vue-grouped-year-tournaments", year=year)
    return parse_year(r.json()) if r is not None else []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", type=int, default=2016)
    ap.add_argument("--to", dest="end", type=int, default=2026)
    ap.add_argument("--db")
    ap.add_argument("--csv")
    a = ap.parse_args()
    client = Client()
    rows = []
    for y in range(a.start, a.end + 1):
        year_rows = fetch_year(client, y)
        rows += year_rows
        print(y, len(year_rows), "站")
    if a.db:
        con = connect(a.db)
        for r in rows:
            upsert_tournament(con, r)
        con.commit()
    if a.csv:
        cols = ["tournament_id", "grade", "level", "category", "name", "start_date", "end_date",
                "country", "status", "code", "source_url"]
        with open(a.csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        print("CSV →", a.csv)


if __name__ == "__main__":
    main()
