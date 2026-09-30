"""每週排名重建（交接單 002 第 4 步）：用 tournament_result 自己算世界排名，只輸出前 100 名。

規則（BWF Statutes 5.3.3.1 第 5、6 條）：
  - 基準日往回 52 週內的成績；超過 10 站只取最好的 10 站加總
  - 同分時參賽站數多者在前；站數也相同則並列（1, 2, 2, 4 …）
  - 雙打以組合為單位；計算全部組合，排序後只存前 100 名（2026-09-30 Raymond 決議）
  - 2020-03-18 至 2021-02-02 排名凍結：沿用凍結前一週的排名，method = frozen
  - 2016 年的週次資料不足（沒有 2015 年的成績），method = insufficient

基準日用週二（2019 年起 BWF 每週二發布）。成績以該組合在該站最後一場的日期為準，
基準日「之前」結束的成績才算（週二發布的排名含前一個週末結束的賽事）。

用法：
  python -m brief.ranking_estimate --db data/brief.db --from 2017-01-03 --to 2026-09-29
  python -m brief.ranking_estimate --db data/brief.db --official-weeks     # 只算有官方快照的週次（驗證用）
"""
from __future__ import annotations

import argparse
import bisect
import datetime as dt
from collections import defaultdict

from brief.crawler import connect

TOP_N = 100
BEST_OF = 10
WINDOW = dt.timedelta(weeks=52)
FREEZE_START, FREEZE_END = dt.date(2020, 3, 18), dt.date(2021, 2, 2)
RELIABLE_FROM = dt.date(2017, 1, 1)

ESTIMATE_TABLE = """
CREATE TABLE IF NOT EXISTS ranking_estimate (
    week_date       TEXT NOT NULL,
    event           TEXT NOT NULL,
    pairing_id      INTEGER NOT NULL REFERENCES pairing(pairing_id),
    rank            INTEGER NOT NULL,
    points          INTEGER NOT NULL,
    tournaments     INTEGER NOT NULL,
    method          TEXT NOT NULL,                -- computed / frozen / insufficient
    PRIMARY KEY (week_date, event, pairing_id)
);
"""


def tuesdays(start: dt.date, end: dt.date) -> list[dt.date]:
    d = start + dt.timedelta(days=(1 - start.weekday()) % 7)
    out = []
    while d <= end:
        out.append(d)
        d += dt.timedelta(weeks=1)
    return out


def rank_list(scores: dict[int, tuple[int, int]], top_n: int = TOP_N) -> list[tuple[int, int, int, int]]:
    """{pairing: (points, tournaments)} → [(rank, pairing, points, tournaments)]，並列同名次，只留前 top_n 名。"""
    ordered = sorted(scores.items(), key=lambda kv: (-kv[1][0], -kv[1][1], kv[0]))
    out, prev, rank = [], None, 0
    for i, (pid, (pts, n)) in enumerate(ordered, 1):
        if (pts, n) != prev:
            rank, prev = i, (pts, n)
        if rank > top_n:
            break
        out.append((rank, pid, pts, n))
    return out


class Estimator:
    """把某項目的全部成績載入記憶體，依日期快速取 52 週視窗。"""

    def __init__(self, rows: list[tuple]):
        # rows: (result_date, pairing_id, points[, is_team])；團體賽 52 週內只取最好的一次（規章 7.2）
        self.rows = sorted((dt.date.fromisoformat(r[0]), r[1], r[2] or 0, bool(r[3]) if len(r) > 3 else False)
                           for r in rows)
        self.dates = [r[0] for r in self.rows]

    def scores(self, week: dt.date) -> dict[int, tuple[int, int]]:
        lo = bisect.bisect_right(self.dates, week - WINDOW)
        hi = bisect.bisect_left(self.dates, week)          # 基準日當天結束的不算
        per: dict[int, list[int]] = defaultdict(list)
        team: dict[int, int] = {}
        for _, pid, pts, is_team in self.rows[lo:hi]:
            if is_team:
                team[pid] = max(team.get(pid, 0), pts)
            else:
                per[pid].append(pts)
        for pid, pts in team.items():
            per[pid].append(pts)
        return {pid: (sum(sorted(p, reverse=True)[:BEST_OF]), len(p)) for pid, p in per.items()}


def compute(con, weeks: list[dt.date], events=("MS", "WS", "MD", "WD", "XD")) -> int:
    con.executescript(ESTIMATE_TABLE)
    n = 0
    for event in events:
        est = Estimator(con.execute("SELECT result_date, pairing_id, points, round_reached = 'TEAM' FROM tournament_result "
                                    "WHERE event=?", (event,)).fetchall())
        frozen_list = None
        for week in weeks:
            if FREEZE_START <= week <= FREEZE_END:
                if frozen_list is None:
                    frozen_list = rank_list(est.scores(FREEZE_START))
                ranked, method = frozen_list, "frozen"
            else:
                ranked = rank_list(est.scores(week))
                method = "computed" if week >= RELIABLE_FROM else "insufficient"
            con.execute("DELETE FROM ranking_estimate WHERE week_date=? AND event=?", (week.isoformat(), event))
            con.executemany(
                "INSERT INTO ranking_estimate VALUES (?,?,?,?,?,?,?)",
                [(week.isoformat(), event, pid, rank, pts, cnt, method) for rank, pid, pts, cnt in ranked])
            n += len(ranked)
        con.commit()
    return n


def official_weeks(con) -> list[dt.date]:
    return [dt.date.fromisoformat(r[0]) for r in
            con.execute("SELECT DISTINCT week_date FROM ranking_snapshot ORDER BY 1")]


def main():
    ap = argparse.ArgumentParser(description="每週排名重建（前 100 名）")
    ap.add_argument("--db", default="brief.db")
    ap.add_argument("--from", dest="start", default="2016-01-05")
    ap.add_argument("--to", dest="end")
    ap.add_argument("--official-weeks", action="store_true", help="只算有官方快照的週次")
    a = ap.parse_args()
    con = connect(a.db)
    if a.official_weeks:
        weeks = official_weeks(con)
    else:
        end = dt.date.fromisoformat(a.end) if a.end else dt.date.today()
        weeks = tuesdays(dt.date.fromisoformat(a.start), end)
    print(f"{len(weeks)} 週，寫入 {compute(con, weeks)} 筆")


if __name__ == "__main__":
    main()
