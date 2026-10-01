"""Grade 3（International Challenge / Series）的日報規則（2026-09-30 Raymond 決議）

Grade 3 照常抓、照常存，但不推進日報，只推下列例外（規則明確，不交給 LLM 判斷）：
  1. 值得一提：選手曾在 Super 750 以上（或 Grade 1、年終總決賽）打進八強，現在出現在 IC / IS
  2. 值得一提：選手在官方排名曾進前 30 名，現在出現在 IC / IS
  3. 中華台北選手在 IC / IS 拿到冠軍、亞軍、季軍（四強敗者）；賽事還在進行時先推「確定至少季軍／亞軍」
每則例外附一行理由；日報結尾另列「今天另有 IC／IS 共 N 場，已存入資料庫」。
"""
from __future__ import annotations

from brief import zh

GRADE3 = {"IC", "IS"}
NO_PUSH = {"FS"}          # Future Series：只存不推，用於排名重建（2026-09-30 22:35）
HIGH_LEVELS = {"S1000", "S750", "WTF", "G1_IND", "G1_EVENT", "SSP"}    # SSP：2017 年以前的 Superseries Premier
DEEP_ROUNDS = {"W": "奪冠", "F": "打進決賽", "SF": "打進四強", "QF": "打進八強"}
DEEP_ORDER = ["W", "F", "SF", "QF"]
RANK_TOP = 30
HOME = "TPE"


def career_reason(con, player_id: int, before: str) -> str | None:
    """選手（任何項目、任何搭檔）在 before 之前，於高層級賽事的最佳成績。"""
    try:
        rows = con.execute(
            f"""SELECT r.round_reached, t.name, t.level, r.result_date
                FROM tournament_result r JOIN tournament t USING (tournament_id)
                JOIN pairing p ON p.pairing_id = r.pairing_id
                WHERE ? IN (p.player_a_id, p.player_b_id) AND r.result_date < ?
                  AND t.level IN ({",".join("?" * len(HIGH_LEVELS))})
                  AND r.round_reached IN ('W', 'F', 'SF', 'QF')""",
            (player_id, before, *sorted(HIGH_LEVELS))).fetchall()
    except Exception:  # noqa: BLE001 — 還沒有 tournament_result 表
        return None
    if not rows:
        return None
    rnd, name, level, _ = min(rows, key=lambda r: (DEEP_ORDER.index(r[0]), r[3]))
    return f"曾在 {zh.tournament(name)}（{zh.level(level)}）{DEEP_ROUNDS[rnd]}"


def rank_reason(con, player_id: int, before: str) -> str | None:
    row = con.execute(
        """SELECT s.rank, s.event, s.week_date FROM ranking_snapshot s JOIN pairing p ON p.pairing_id = s.pairing_id
           WHERE ? IN (p.player_a_id, p.player_b_id) AND s.week_date < ? AND s.rank <= ?
           ORDER BY s.rank, s.week_date LIMIT 1""", (player_id, before, RANK_TOP)).fetchone()
    if not row:
        return None
    rank, event, week = row
    return f"前世界第 {rank} 名（{zh.event(event)}，{week} 官方排名）"


def notable_reason(con, player_id: int, before: str) -> str | None:
    reasons = [r for r in (rank_reason(con, player_id, before), career_reason(con, player_id, before)) if r]
    return "、".join(reasons) or None


def podium_status(matches: list[dict]) -> dict[tuple, str]:
    """一站 IC/IS 的新比賽 → {(項目, pairing_id): 名次}，只看中華台北選手。
    matches 需有 event、round、winner、loser（_side 的結果，含 pairing_id 與 home）。"""
    order = {"冠軍": 5, "亞軍": 4, "確定至少亞軍": 3, "季軍": 2, "確定至少季軍": 1}
    out: dict[tuple, str] = {}

    def put(side, event, status):
        if side["home"]:
            key = (event, side["pairing_id"])
            if order[status] > order.get(out.get(key), 0):
                out[key] = status

    for m in matches:
        rnd = m["round"]
        if rnd in ("Final", "F"):
            put(m["winner"], m["event"], "冠軍")
            put(m["loser"], m["event"], "亞軍")
        elif rnd == "SF":
            put(m["loser"], m["event"], "季軍")
            put(m["winner"], m["event"], "確定至少亞軍")
        elif rnd == "QF":
            put(m["winner"], m["event"], "確定至少季軍")
    return out


def podium_line(tournament: str, level: str, matches: list[dict]) -> str | None:
    status = podium_status(matches)
    if not status:
        return None
    names = {}
    for m in matches:
        for side in (m["winner"], m["loser"]):
            names[side["pairing_id"]] = side["name"]
    ev_order = ["MS", "WS", "MD", "WD", "XD"]
    sep = lambda n: " " if n and n[-1].isascii() else ""      # 「林俊易（LIN Chun-Yi）男單亞軍」；英文名結尾才空一格
    parts = [f"{names[pid]}{sep(names[pid])}{zh.event(ev)}{st}" for (ev, pid), st in
             sorted(status.items(), key=lambda kv: (ev_order.index(kv[0][0]) if kv[0][0] in ev_order else 9, kv[0][1]))]
    return f"- IC／IS｜{zh.tournament(tournament)}（{zh.level(level)}）：" + "、".join(parts)


# ---------------------------------------------------------------- 空檔週（2026-09-30 23:15 決議）
PROMOTE = {"IC", "IS"}                   # 空檔週可以升格的層級（FS 不收）
LATE_DAY_ROUNDS = ("SF", "Semi-finals", "Final", "F")   # notes 21:05：IC／IS 四強日、決賽日不論空檔週都由 watch 逐站發
# 已取消：狀態欄是 cancelled／postponed，或名稱帶 (Cancelled)（有 5 站狀態還是 normal，例：Azerbaijan Caspian Cup 2026）
ACTIVE_SQL = "COALESCE(status, '') NOT IN ('cancelled', 'postponed') AND name NOT LIKE '%Cancelled%'"


def late_day(con, tournament_id: int, day: str) -> bool:
    """這一站這一天有四強或決賽。"""
    return con.execute(f"SELECT 1 FROM match WHERE tournament_id=? AND match_date=? AND round IN ({','.join('?' * len(LATE_DAY_ROUNDS))}) "
                       "LIMIT 1", (tournament_id, day, *LATE_DAY_ROUNDS)).fetchone() is not None
NOT_PUSH_LEVELS = ("IC", "IS", "FS")


def week_bounds(day: str) -> tuple[str, str]:
    """BWF 週：週一到週日。"""
    import datetime as dt
    d = dt.date.fromisoformat(day)
    monday = d - dt.timedelta(days=d.weekday())
    return monday.isoformat(), (monday + dt.timedelta(days=6)).isoformat()


def quiet_week(con, day: str) -> bool:
    """這一週完全沒有推送層級賽事（賽期與這週有重疊就算有）→ IC／IS 升格，由 brief.watch 逐站發。"""
    monday, sunday = week_bounds(day)
    n = con.execute(
        f"""SELECT COUNT(*) FROM tournament
            WHERE level IS NOT NULL AND level NOT IN ({",".join("?" * len(NOT_PUSH_LEVELS))})
              AND {ACTIVE_SQL}
              AND start_date <= ? AND end_date >= ?""", (*NOT_PUSH_LEVELS, sunday, monday)).fetchone()[0]
    return n == 0
