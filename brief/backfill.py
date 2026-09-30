"""羽球日報 Agent：十年比賽回補（交接單 002 第 1 步）

讀 tournament 表（由 brief.calendar 建立），由新到舊逐站呼叫 crawler.crawl_known。
  - 只抓已結束的賽事；跳過 cancelled / postponed（進行中的交給 brief.daily）
  - 可中斷、可續跑：每站的結果記在 backfill_status，重跑時跳過 done / empty
  - 請求間隔維持 2 秒（crawler.Client）；2016–2026 約 980 站、5,100 個請求、3 小時

用法：
  python -m brief.backfill run --db data/brief.db                 # 全部
  python -m brief.backfill run --db data/brief.db --year 2019 --limit 20
  python -m brief.backfill run --db data/brief.db --retry-failed  # 重試失敗的站
  python -m brief.backfill report --db data/brief.db              # 各年、各層級的站數與場數
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections import defaultdict

from brief.crawler import Client, connect, crawl_known

SKIP_STATUS = ("cancelled", "postponed")

BACKFILL_TABLE = """
CREATE TABLE IF NOT EXISTS backfill_status (
    tournament_id   INTEGER PRIMARY KEY REFERENCES tournament(tournament_id),
    status          TEXT NOT NULL,                -- done：有比賽；empty：API 沒有任何比賽；failed：出錯
    matches         INTEGER NOT NULL DEFAULT 0,   -- 這次寫入（含更新）的已完成單場
    team_ties       INTEGER NOT NULL DEFAULT 0,
    unfinished      INTEGER NOT NULL DEFAULT 0,
    error           TEXT,
    finished_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def candidates(con, today: dt.date, year: int | None = None, retry_failed: bool = False) -> list[dict]:
    """還沒回補的已結束賽事，由新到舊。"""
    con.executescript(BACKFILL_TABLE)
    skip = ("done", "empty") if retry_failed else ("done", "empty", "failed")
    rows = con.execute(
        f"""SELECT t.tournament_id, t.code, t.name, t.grade, t.level, t.status, t.start_date, t.end_date, t.source_url
            FROM tournament t LEFT JOIN backfill_status b USING (tournament_id)
            WHERE t.end_date < ? AND COALESCE(t.status, '') NOT IN ({",".join("?" * len(SKIP_STATUS))})
              AND (b.status IS NULL OR b.status NOT IN ({",".join("?" * len(skip))}))
              AND (? IS NULL OR substr(t.start_date, 1, 4) = ?)
            ORDER BY t.start_date DESC, t.tournament_id DESC""",
        (today.isoformat(), *SKIP_STATUS, *skip, year, str(year) if year else None)).fetchall()
    keys = ["tournament_id", "code", "name", "grade", "level", "status", "start_date", "end_date", "source_url"]
    return [dict(zip(keys, r)) for r in rows]


def _record(con, tid: int, status: str, res: dict | None = None, error: str | None = None) -> None:
    res = res or {}
    con.execute(
        """INSERT INTO backfill_status (tournament_id, status, matches, team_ties, unfinished, error)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(tournament_id) DO UPDATE SET status=excluded.status, matches=excluded.matches,
             team_ties=excluded.team_ties, unfinished=excluded.unfinished, error=excluded.error,
             finished_at=datetime('now')""",
        (tid, status, res.get("stored", 0), res.get("team_ties", 0), res.get("skipped_unfinished", 0), error))
    con.commit()


def run(con, client: Client, today: dt.date, year: int | None = None, limit: int | None = None,
        retry_failed: bool = False, verbose: bool = True) -> dict:
    todo = candidates(con, today, year, retry_failed)[:limit]
    summary = defaultdict(int)
    for i, t in enumerate(todo, 1):
        tid = t["tournament_id"]
        try:
            if not t["code"]:
                raise ValueError("缺少 GUID")
            res = crawl_known(client, con, t, verbose=False)
            found = res["stored"] + res["skipped_unfinished"] + res["team_ties"] + res["skipped_no_players"]
            status = "done" if found else "empty"
            _record(con, tid, status, res)
        except Exception as e:  # noqa: BLE001 — 一站失敗不擋其他站，之後用 --retry-failed 重試
            status, res = "failed", {}
            _record(con, tid, status, error=repr(e)[:500])
        summary[status] += 1
        summary["matches"] += res.get("stored", 0)
        if verbose:
            print(f"[{i}/{len(todo)}] {tid} {t['start_date']} {t['name']}：{status}，{res.get('stored', 0)} 場",
                  flush=True)
    return dict(summary)


def report(con) -> str:
    """各年、各層級的站數與比賽場數（以資料庫實際內容計算）。"""
    con.executescript(BACKFILL_TABLE)
    rows = con.execute(
        """SELECT substr(t.start_date, 1, 4) AS y, t.level, COUNT(DISTINCT t.tournament_id),
                  SUM(CASE WHEN b.status = 'done' THEN 1 ELSE 0 END),
                  SUM(CASE WHEN b.status = 'empty' THEN 1 ELSE 0 END),
                  SUM(CASE WHEN b.status = 'failed' THEN 1 ELSE 0 END),
                  (SELECT COUNT(*) FROM match m JOIN tournament t2 USING (tournament_id)
                   WHERE substr(t2.start_date, 1, 4) = substr(t.start_date, 1, 4) AND t2.level IS t.level)
           FROM tournament t LEFT JOIN backfill_status b USING (tournament_id)
           WHERE COALESCE(t.status, '') NOT IN ('cancelled', 'postponed')
           GROUP BY y, t.level ORDER BY y, t.level""").fetchall()
    lines = ["| 年 | 層級 | 站數 | done | empty | failed | 比賽場數 |", "|---|---|---|---|---|---|---|"]
    for y, level, n, done, empty, failed, matches in rows:
        lines.append(f"| {y} | {level} | {n} | {done} | {empty} | {failed} | {matches} |")
    failed_rows = con.execute(
        """SELECT b.tournament_id, t.name, b.error FROM backfill_status b JOIN tournament t USING (tournament_id)
           WHERE b.status = 'failed' ORDER BY t.start_date""").fetchall()
    if failed_rows:
        lines += ["", "失敗的站："] + [f"- {tid} {name}：{err}" for tid, name, err in failed_rows]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="十年比賽回補")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--db", default="brief.db")
    r.add_argument("--year", type=int)
    r.add_argument("--limit", type=int)
    r.add_argument("--retry-failed", action="store_true")
    p = sub.add_parser("report")
    p.add_argument("--db", default="brief.db")
    a = ap.parse_args()
    con = connect(a.db)
    if a.cmd == "report":
        print(report(con))
        return
    from brief.daily import today_taipei
    res = run(con, Client(), today_taipei(), a.year, a.limit, a.retry_failed)
    print("完成：", res)
    sys.exit(1 if res.get("failed") else 0)


if __name__ == "__main__":
    main()
