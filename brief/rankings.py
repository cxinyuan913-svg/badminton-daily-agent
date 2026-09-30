"""羽球日報 Agent：世界排名快照爬蟲

資料來源（2026-09-30 實測）：
  週次清單  https://extranet-lv.bwfbadminton.com/api/vue-rankingweek?rankId=2
  排名表    https://extranet-lv.bwfbadminton.com/api/vue-rankingtable
            ?rankId=2&catId={6-10}&publicationId={id}&doubles={true|false}
             &searchKey=&pageKey={每頁筆數}&page={頁碼}&drawCount=1
  catId：6=MS、7=WS、8=MD、9=WD、10=XD

API 只保留最近約 60 週，所以這支程式要每週跑一次，把快照存進資料庫。
第一次執行會回補 API 還留著的所有週次。

用法：
  python -m brief.rankings                       # 補齊所有缺少的週次（每項目前 500 名）
  python -m brief.rankings --latest              # 只抓最新一週
  python -m brief.rankings --max-rank 1000
"""
from __future__ import annotations

import argparse

from brief.crawler import API, Client, connect, pairing_id

CATEGORIES = {6: "MS", 7: "WS", 8: "MD", 9: "WD", 10: "XD"}
DOUBLES = {"MD", "WD", "XD"}
PAGE_SIZE = 100


def list_weeks(client: Client) -> list[dict]:
    r = client.get(f"{API}/vue-rankingweek", rankId=2)
    return r.json() if r is not None else []


def fetch_page(client: Client, publication_id: int, cat_id: int, page: int) -> dict:
    r = client.get(f"{API}/vue-rankingtable", rankId=2, catId=cat_id, publicationId=publication_id,
                   doubles=str(CATEGORIES[cat_id] in DOUBLES).lower(), searchKey="",
                   pageKey=PAGE_SIZE, page=page, drawCount=1)
    return r.json()["results"] if r is not None else {"data": [], "last_page": 0}


def ensure_player(con, player_id: int, model: dict | None):
    con.execute("INSERT OR IGNORE INTO player (player_id, slug) VALUES (?, ?)",
                (player_id, (model or {}).get("slug")))


def store_rows(con, week_date: str, event: str, rows: list[dict]) -> int:
    n = 0
    for row in rows:
        ids = [row["player1_id"]] + ([row["player2_id"]] if row.get("player2_id") else [])
        ensure_player(con, int(row["player1_id"]), row.get("player1_model"))
        if row.get("player2_id"):
            ensure_player(con, int(row["player2_id"]), row.get("player2_model"))
        pid = pairing_id(con, [int(i) for i in ids])
        con.execute(
            """INSERT INTO ranking_snapshot (week_date, publication_id, event, pairing_id, rank,
                                             rank_previous, points, tournaments)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(week_date, event, pairing_id) DO UPDATE SET
                 rank=excluded.rank, rank_previous=excluded.rank_previous,
                 points=excluded.points, tournaments=excluded.tournaments""",
            (week_date, row.get("ranking_publication_id"), event, pid, int(row["rank"]),
             row.get("rank_previous"), float(row["points"]) if row.get("points") else None,
             row.get("tournaments")),
        )
        n += 1
    return n


def crawl_week(client: Client, con, week: dict, max_rank: int = 500, verbose=True) -> int:
    week_date = week["date"][:10]
    total = 0
    for cat_id, event in CATEGORIES.items():
        page = 1
        while True:
            res = fetch_page(client, week["id"], cat_id, page)
            rows = [r for r in res.get("data", []) if int(r["rank"]) <= max_rank]
            total += store_rows(con, week_date, event, rows)
            con.commit()
            if not rows or page >= res.get("last_page", 0) or len(rows) < len(res.get("data", [])):
                break
            page += 1
    if verbose:
        print(f"{week['display']}: {total} 筆")
    return total


def weeks_missing(con, weeks: list[dict]) -> list[dict]:
    have = {r[0] for r in con.execute("SELECT DISTINCT week_date FROM ranking_snapshot")}
    return [w for w in weeks if w["date"][:10] not in have]


def rank_lookup(con, pairing: int, event: str, on_date: str) -> tuple[int | None, str | None]:
    """比賽當天適用的排名與來源：(名次, "official" | "estimate" | None)。
    先找該日期（含）之前最近一週的官方快照，再看這個組合在那一週的名次（不在表上 = 500 名外，回傳 None）。
    日期早於官方快照（API 只留約 60 週）時，改查 ranking_estimate（自行重建，只有前 100 名）。"""
    week = con.execute("SELECT MAX(week_date) FROM ranking_snapshot WHERE event=? AND week_date<=?",
                       (event, on_date)).fetchone()[0]
    if week:
        row = con.execute("SELECT rank FROM ranking_snapshot WHERE week_date=? AND event=? AND pairing_id=?",
                          (week, event, pairing)).fetchone()
        return (row[0], "official") if row else (None, None)
    try:
        week = con.execute("SELECT MAX(week_date) FROM ranking_estimate WHERE event=? AND week_date<=?",
                           (event, on_date)).fetchone()[0]
    except Exception:  # noqa: BLE001 — 還沒建立 ranking_estimate 表
        return None, None
    if not week:
        return None, None
    row = con.execute("SELECT rank FROM ranking_estimate WHERE week_date=? AND event=? AND pairing_id=?",
                      (week, event, pairing)).fetchone()
    return (row[0], "estimate") if row else (None, None)


def rank_on(con, pairing: int, event: str, on_date: str):
    """只回傳名次（見 rank_lookup）。"""
    return rank_lookup(con, pairing, event, on_date)[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="brief.db")
    ap.add_argument("--latest", action="store_true")
    ap.add_argument("--max-rank", type=int, default=500)
    a = ap.parse_args()
    con = connect(a.db)
    client = Client()
    weeks = list_weeks(client)
    todo = weeks[:1] if a.latest else weeks_missing(con, weeks)
    print(f"API 保留 {len(weeks)} 週，這次要抓 {len(todo)} 週")
    for w in todo:
        crawl_week(client, con, w, a.max_rank)


if __name__ == "__main__":
    main()
