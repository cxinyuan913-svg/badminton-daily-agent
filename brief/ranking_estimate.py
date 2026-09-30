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
    """把某項目的全部成績載入記憶體，依日期快速取 52 週視窗。
    rows：(result_date, pairing_id, points[, is_team[, expires[, group]]])
      is_team：團體賽 52 週內只取最好的一次（§7.2）
      expires：同一站下一屆結束的日期；之後的週次不再計入（V6.0 §2.2「到下一屆舉辦或 52 週，以先到者為準」）
      group：洲際錦標賽／洲際綜合運動會個人賽的（層級, 洲），52 週內每組只計最新一次（§9.1.3、§9.1.4）"""

    def __init__(self, rows: list[tuple]):
        def norm(r):
            r = tuple(r) + (None,) * (6 - len(r))
            return (dt.date.fromisoformat(r[0]), r[1], r[2] or 0, bool(r[3]),
                    dt.date.fromisoformat(r[4]) if r[4] else None, r[5])
        self.rows = sorted((norm(r) for r in rows), key=lambda r: (r[0], r[1]))
        self.dates = [r[0] for r in self.rows]

    def scores(self, week: dt.date) -> dict[int, tuple[int, int]]:
        lo = bisect.bisect_right(self.dates, week - WINDOW)
        hi = bisect.bisect_left(self.dates, week)          # 基準日當天結束的不算
        per: dict[int, list[int]] = defaultdict(list)
        team: dict[int, int] = {}
        latest: dict[tuple, tuple] = {}
        for date, pid, pts, is_team, expires, group in self.rows[lo:hi]:
            if expires is not None and week > expires:
                continue                                   # 下一屆已經舉辦，上一屆的成績失效
            if is_team:
                team[pid] = max(team.get(pid, 0), pts)
            elif group:
                if (pid, group) not in latest or latest[(pid, group)][0] <= date:
                    latest[(pid, group)] = (date, pts)     # 同一洲只計最新一次
            else:
                per[pid].append(pts)
        for (pid, _), (_, pts) in latest.items():
            per[pid].append(pts)
        for pid, pts in team.items():
            per[pid].append(pts)
        return {pid: (sum(sorted(p, reverse=True)[:BEST_OF]), len(p)) for pid, p in per.items()}


def tournament_meta(con) -> dict[int, tuple[str | None, tuple | None]]:
    """{tournament_id: (下一屆結束日, 洲際分組)}。
    下一屆：同一站（series_key）、同一層級、下一次開打的賽事；只在 V6.0（2024 第 17 週後舉辦的下一屆）套用 §2.2。"""
    from brief import ranking_points as rp
    rows = con.execute("SELECT tournament_id, name, level, start_date, end_date FROM tournament "
                       "WHERE start_date IS NOT NULL AND COALESCE(status, '') NOT IN ('cancelled', 'postponed') "
                       "ORDER BY start_date").fetchall()
    by_series: dict[tuple, list] = defaultdict(list)
    for tid, name, level, start, end in rows:
        by_series[(rp.series_key(name), level)].append((start, end, tid))
    meta = {}
    for tid, name, level, start, end in rows:
        series = by_series[(rp.series_key(name), level)]
        nxt = next((e for s, e, t in series if s > start), None)
        if nxt and nxt < rp.VERSIONS[-1][0].isoformat():
            nxt = None
        group = (level, rp.continent(name)) if level in ("CONT_IND", "MULTI") and rp.continent(name) else None
        meta[tid] = (nxt, group)
    return meta


def compute(con, weeks: list[dt.date], events=("MS", "WS", "MD", "WD", "XD"), exclude_levels=()) -> int:
    """exclude_levels：驗證分項用，暫時不計某些層級的成績（例如比較有無 Future Series）。"""
    con.executescript(ESTIMATE_TABLE)
    meta = tournament_meta(con)
    n = 0
    level_filter = f"AND COALESCE(t.level, '') NOT IN ({','.join('?' * len(exclude_levels))})" if exclude_levels else ""
    for event in events:
        est = Estimator([(d, pid, pts, team, *meta.get(tid, (None, None))) for d, pid, pts, team, tid in con.execute(
            "SELECT r.result_date, r.pairing_id, r.points, r.round_reached = 'TEAM', r.tournament_id FROM tournament_result r "
            f"LEFT JOIN tournament t USING (tournament_id) WHERE r.event=? {level_filter}", (event, *exclude_levels))])
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
