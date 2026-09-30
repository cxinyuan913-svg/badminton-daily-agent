"""Super 1000 分級反推（2026-09-30 22:45 決議）→ config/s1000_grade.csv

V6.0 §6.3：2024 第 17 週起 Super 1000 依額外加碼獎金分三級：≥ US$500K → 13500、US$250K–499,999 → 12700、
沒有加碼 → 12000。各站加碼金額不在 API 裡，頒獎台 API 的積分欄實測仍是舊的 12000 或空白，不能用。

做法：對每一站、每一種分級，比較打過這站的組合在「賽前最後一週 → 賽後第一週」的官方積分變化與估算變化，
取平均誤差最小的分級（依據欄記下三種誤差）。只比變化量，其他站的系統性誤差會抵消。
前後兩週不都在官方快照範圍內的（太早），沿用同一站最近一年反推的結果，標「推定」。

用法：python -m brief.s1000_grade --db data/brief.db
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt

from brief import ranking_points as rp
from brief.crawler import connect
from brief.ranking_estimate import Estimator, tournament_meta

GRADES = ["13500", "12700", "12000"]


def _rows(con, event: str):
    return con.execute(
        """SELECT r.result_date, r.pairing_id, r.points, r.round_reached = 'TEAM', r.tournament_id, r.round_reached
           FROM tournament_result r WHERE r.event=?""", (event,)).fetchall()


def infer(con) -> list[dict]:
    """用「賽前最後一週 → 賽後第一週」的積分變化比對：其他站的系統性誤差（例如缺站）在前後兩週會互相抵消，
    只剩這一站的分級差異。兩週都要在官方快照範圍內；範圍外的沿用同一站最近一年，標「推定」。"""
    weeks = [dt.date.fromisoformat(w) for (w,) in con.execute("SELECT DISTINCT week_date FROM ranking_snapshot ORDER BY 1")]
    s1000 = con.execute(
        """SELECT tournament_id, name, start_date, end_date FROM tournament
           WHERE level='S1000' AND start_date >= '2024-04-22' AND end_date < ? ORDER BY start_date""",
        (weeks[-1].isoformat(),)).fetchall()
    rows_by_event = {ev: _rows(con, ev) for ev in ("MS", "WS", "MD", "WD", "XD")}
    meta = tournament_meta(con)
    out = []
    for tid, name, start, end in s1000:
        start_d, end_d = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
        before = max((w for w in weeks if w <= start_d), default=None)
        after = next((w for w in weeks if w > end_d), None)
        if before is None or after is None:
            out.append({"tournament_id": tid, "name": name, "year": start[:4], "grade": None, "basis": "推定"})
            continue
        official = {}
        for ev in rows_by_event:
            b = dict(con.execute("SELECT pairing_id, points FROM ranking_snapshot WHERE week_date=? AND event=?", (before.isoformat(), ev)))
            a = dict(con.execute("SELECT pairing_id, points FROM ranking_snapshot WHERE week_date=? AND event=?", (after.isoformat(), ev)))
            official[ev] = {pid: a[pid] - b[pid] for pid in a.keys() & b.keys()}
        errors = {}
        for g in GRADES:
            row = rp.V2024W17[rp.S1000_LEVEL[g]]
            err, n = 0.0, 0
            for ev, rows in rows_by_event.items():
                mine = {pid for d, pid, pts, team, t, pos in rows if t == tid}
                if not mine:
                    continue
                adj = [(d, pid, (row[rp.POSITIONS.index(pos)] if pos in rp.POSITIONS and rp.POSITIONS.index(pos) < len(row)
                                  else pts) if t == tid else pts, team, *meta.get(t, (None, None)))
                       for d, pid, pts, team, t, pos in rows]
                est = Estimator(adj)
                sb, sa = est.scores(before), est.scores(after)
                for pid in mine & official[ev].keys():
                    err += abs((sa.get(pid, (0, 0))[0] - sb.get(pid, (0, 0))[0]) - official[ev][pid])
                    n += 1
            errors[g] = (err / n, n) if n else None
        valid = [g for g in GRADES if errors[g]]
        best = min(valid, key=lambda g: errors[g][0], default=None)
        basis = (f"官方排名前後週變化（{before}→{after}，{errors[best][1]} 組）；平均誤差 " +
                 "／".join(f"{g}:{errors[g][0]:.0f}" for g in valid)) if best else "推定"
        out.append({"tournament_id": tid, "name": name, "year": start[:4], "grade": best, "basis": basis})
    # 推定：同一站最近一年反推出來的分級
    known = [r for r in out if r["grade"]]
    for r in out:
        if r["grade"] is None:
            same = sorted((k for k in known if rp.series_key(k["name"]) == rp.series_key(r["name"])),
                          key=lambda k: abs(int(k["year"]) - int(r["year"])))
            if same:
                r["grade"], r["basis"] = same[0]["grade"], f"推定：沿用 {same[0]['year']} 年同一站"
            else:
                r["grade"], r["basis"] = "13500", "推定：沒有可反推的同一站，採最高級"
    return out


def main():
    ap = argparse.ArgumentParser(description="Super 1000 分級反推")
    ap.add_argument("--db", default="brief.db")
    ap.add_argument("--out", default=str(rp.S1000_GRADE_CSV))
    a = ap.parse_args()
    rows = infer(connect(a.db))
    with open(a.out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["tournament_id", "name", "year", "grade", "basis"], lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    for r in rows:
        print(r["year"], r["name"], r["grade"], r["basis"])


if __name__ == "__main__":
    main()
