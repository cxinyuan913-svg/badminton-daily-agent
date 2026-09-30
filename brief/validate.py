"""排名重建驗證（交接單 002 第 5 步）：拿官方快照（ranking_snapshot，約 60 週）比對同週的 ranking_estimate。

指標（依項目分開，只看官方前 100 名）：
  - 前 100 名名單重疊率
  - 名次完全相同的比例
  - 名次誤差 ≤ 2 的比例（另列前 50 名，目標暫定 ≥ 90%）
  - 積分平均絕對誤差（兩邊都在前 100 名的組合）
另列差異最大的案例，供找原因。

用法：python -m brief.validate --db data/brief.db [--out docs/ranking-validation-data.md]
"""
from __future__ import annotations

import argparse
from collections import defaultdict

from brief.crawler import connect

EVENTS = ["MS", "WS", "MD", "WD", "XD"]


def compare(con, top: int = 100) -> tuple[dict, list[dict]]:
    weeks = [r[0] for r in con.execute(
        "SELECT DISTINCT s.week_date FROM ranking_snapshot s JOIN ranking_estimate e USING (week_date) ORDER BY 1")]
    stats = {ev: defaultdict(float) for ev in EVENTS}
    cases = []
    for week in weeks:
        for ev in EVENTS:
            off = {pid: (rank, pts) for pid, rank, pts in con.execute(
                "SELECT pairing_id, rank, points FROM ranking_snapshot WHERE week_date=? AND event=? AND rank<=?",
                (week, ev, top))}
            est = {pid: (rank, pts) for pid, rank, pts in con.execute(
                "SELECT pairing_id, rank, points FROM ranking_estimate WHERE week_date=? AND event=? AND rank<=?",
                (week, ev, top))}
            if not off:
                continue
            s = stats[ev]
            s["weeks"] += 1
            s["official"] += len(off)
            s["overlap"] += len(off.keys() & est.keys())
            for pid, (rank, pts) in off.items():
                e = est.get(pid)
                diff = abs(e[0] - rank) if e else None
                s["exact"] += diff == 0
                s["within2"] += diff is not None and diff <= 2
                if rank <= 50:
                    s["top50"] += 1
                    s["top50_within2"] += diff is not None and diff <= 2
                if e:
                    s["pts_err"] += abs(e[1] - (pts or 0))
                    s["pts_n"] += 1
                if diff is None or diff > 2:
                    cases.append({"week": week, "event": ev, "pairing_id": pid, "official_rank": rank,
                                  "official_points": pts, "estimate_rank": e[0] if e else None,
                                  "estimate_points": e[1] if e else None,
                                  "gap": diff if diff is not None else 999})
    return stats, cases


def _name(con, pairing_id: int) -> str:
    rows = con.execute("""SELECT COALESCE(pl.name_display, pl.slug, pl.player_id) FROM pairing p
                          JOIN player pl ON pl.player_id IN (p.player_a_id, p.player_b_id)
                          WHERE p.pairing_id=? ORDER BY pl.player_id""", (pairing_id,)).fetchall()
    return " / ".join(str(r[0]) for r in rows)


def report(con, worst: int = 10) -> str:
    stats, cases = compare(con)
    lines = ["| 項目 | 週數 | 前 100 重疊率 | 名次完全相同 | 誤差 ≤ 2 | 前 50 誤差 ≤ 2 | 積分平均絕對誤差 |",
             "|---|---|---|---|---|---|---|"]
    pct = lambda a, b: f"{100 * a / b:.1f}%" if b else "—"
    for ev in EVENTS:
        s = stats[ev]
        if not s["weeks"]:
            continue
        mae = f"{s['pts_err'] / s['pts_n']:.0f}" if s["pts_n"] else "—"
        lines.append(f"| {ev} | {int(s['weeks'])} | {pct(s['overlap'], s['official'])} | {pct(s['exact'], s['official'])} "
                     f"| {pct(s['within2'], s['official'])} | {pct(s['top50_within2'], s['top50'])} | {mae} |")
    # 同一組合只列一次（取差距最大的那週），避免同一個原因洗版
    by_pair = {}
    for c in sorted(cases, key=lambda c: (-c["gap"], c["official_rank"])):
        by_pair.setdefault((c["event"], c["pairing_id"]), c)
    top_cases = sorted(by_pair.values(), key=lambda c: (-c["gap"], c["official_rank"]))[:worst]
    lines += ["", f"差異最大的 {worst} 個組合（每組合取差距最大的一週）：", "",
              "| 週 | 項目 | 選手 | 官方名次 | 官方積分 | 估算名次 | 估算積分 |", "|---|---|---|---|---|---|---|"]
    for c in top_cases:
        lines.append(f"| {c['week']} | {c['event']} | {_name(con, c['pairing_id'])} | {c['official_rank']} "
                     f"| {c['official_points']:.0f} | {c['estimate_rank'] or '前 100 外'} | {c['estimate_points'] or '—'} |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="排名重建驗證")
    ap.add_argument("--db", default="brief.db")
    ap.add_argument("--worst", type=int, default=10)
    a = ap.parse_args()
    print(report(connect(a.db), a.worst))


if __name__ == "__main__":
    main()
