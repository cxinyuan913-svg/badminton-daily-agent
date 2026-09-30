"""明日看點：從賽程挑出值得看的對戰（2026-09-30 Raymond 決議，notes 18:10）

選場規則（有一項符合就列，附一句理由；理由由規則產生，**不用 LLM**，避免捏造戰績）：
  1. 有中華台北選手
  2. 雙方都在世界前 10（官方排名）
  3. 過去 12 個月交手過，且上次是決賽或四強 →「上次○○決賽的重演」
  4. 交手紀錄懸殊但近期弱勢方贏過（總戰績 ≥ 4 場、弱勢方勝率 ≤ 25%，且最近一次是弱勢方贏）
  5. 追蹤中的台灣選手，對手今年打敗過他 → 復仇戰
每場一行，最多 8 場（台灣選手優先，其餘按兩邊排名和），依台灣時間排序。
交手戰績只用資料庫裡的比賽（十年回補完成前可能偏少）。

時間：day-matches 的 matchTimeUtc + 8 小時 = 台灣時間。oopText：
  Starting at … / 無 → 「14:30（台灣）」；Not before … → 「不早於 15:30（台灣）」；
  Followed by → 「約 15:30 後，接第 N 場」（N = 同場地前一場的順序）；Court and time TBA → 「時間未定」
"""
from __future__ import annotations

import datetime as dt

from brief import zh
from brief.rankings import rank_lookup

TAIPEI = dt.timedelta(hours=8)
MAX_LINES = 8
DEEP = {"Final": "決賽", "F": "決賽", "SF": "四強"}
DISCIPLINE = {"Men's Singles": "MS", "Women's Singles": "WS", "Men's Doubles": "MD",
              "Women's Doubles": "WD", "Mixed Doubles": "XD"}


def _dt(s: str | None) -> dt.datetime | None:
    return dt.datetime.fromisoformat(s) if s else None


def taiwan_time(m: dict) -> dt.datetime | None:
    utc = _dt(m.get("matchTimeUtc"))
    return utc + TAIPEI if utc else None


def local_offset(m: dict) -> dt.timedelta | None:
    """賽事當地與 UTC 的時差（matchTime − matchTimeUtc）。"""
    local, utc = _dt(m.get("matchTime")), _dt(m.get("matchTimeUtc"))
    return local - utc if local and utc else None


def time_label(m: dict) -> str:
    text = (m.get("oopText") or "").strip()
    tw = taiwan_time(m)
    if text.startswith("Court and time TBA") or tw is None:
        return "時間未定"
    hhmm = tw.strftime("%H:%M")
    if text.startswith("Followed by"):
        n = int(m.get("oopRound") or 1) - 1
        return f"約 {hhmm} 後，接第 {n} 場" if n >= 1 else f"約 {hhmm}（台灣）"
    if text.startswith("Not before"):
        return f"不早於 {hhmm}（台灣）"
    return f"{hhmm}（台灣）"


def _ids(team: dict | None) -> list[int]:
    return sorted(int(p["id"]) for p in (team or {}).get("players") or [])


def _pairing(con, ids: list[int]) -> int | None:
    if not ids:
        return None
    a, b = ids[0], (ids[1] if len(ids) > 1 else None)
    row = con.execute("SELECT pairing_id FROM pairing WHERE player_a_id=? AND player_b_id IS ?", (a, b)).fetchone()
    return row[0] if row else None


def h2h(con, p1: int, p2: int, before: str) -> list[dict]:
    """兩個組合在 before 之前的交手，由新到舊。"""
    rows = con.execute(
        """SELECT m.match_date, m.round, t.name, CASE WHEN (m.side1_id=? AND m.winner_side=1) OR (m.side2_id=? AND m.winner_side=2)
                  THEN 1 ELSE 2 END
           FROM match m JOIN tournament t USING (tournament_id)
           WHERE ((m.side1_id=? AND m.side2_id=?) OR (m.side1_id=? AND m.side2_id=?)) AND m.match_date < ?
             AND m.winner_side IN (1, 2)
           ORDER BY m.match_date DESC, m.match_id DESC""", (p1, p1, p1, p2, p2, p1, before)).fetchall()
    return [{"date": d, "round": r, "tournament": t, "winner": w} for d, r, t, w in rows]


def reasons(con, m: dict, on: str, tracked_tpe: set[int]) -> tuple[list[str], dict]:
    """回傳（理由, 資訊）。資訊含雙方組合、排名、國家，供排序與顯示。"""
    event = DISCIPLINE.get(m.get("matchTypeValue") or m.get("eventName"), m.get("eventName"))
    ids1, ids2 = _ids(m.get("team1")), _ids(m.get("team2"))
    p1, p2 = _pairing(con, ids1), _pairing(con, ids2)
    r1 = rank_lookup(con, p1, event, on)[0] if p1 else None
    r2 = rank_lookup(con, p2, event, on)[0] if p2 else None
    c1 = {p.get("countryCode") for p in (m.get("team1") or {}).get("players") or []}
    c2 = {p.get("countryCode") for p in (m.get("team2") or {}).get("players") or []}
    info = {"event": event, "p1": p1, "p2": p2, "r1": r1, "r2": r2, "tpe": "TPE" in c1 | c2,
            "ids1": ids1, "ids2": ids2, "flip": "TPE" in c2 and "TPE" not in c1}   # 台灣選手在第二方時對調，戰績以台灣選手角度寫
    out = []
    if info["tpe"]:
        out.append("中華台北")
    if r1 and r2 and r1 <= 10 and r2 <= 10:
        out.append("前 10 對決")
    games = h2h(con, p1, p2, on) if p1 and p2 else []
    if games:
        wins1 = sum(g["winner"] == 1 for g in games)
        wins2 = len(games) - wins1
        last = games[0]
        cutoff = (dt.date.fromisoformat(on) - dt.timedelta(days=365)).isoformat()
        if last["date"] >= cutoff and last["round"] in DEEP:
            out.append(f"上次 {zh.tournament(last['tournament'])}{DEEP[last['round']]}的重演")
        weak = 1 if wins1 < wins2 else 2 if wins2 < wins1 else None
        if weak and len(games) >= 4 and min(wins1, wins2) / len(games) <= 0.25 and last["winner"] == weak:
            out.append("交手懸殊，但最近一次弱勢方贏")
        for side, ids in ((1, ids1), (2, ids2)):
            if set(ids) & tracked_tpe:
                year = on[:4]
                if any(g["winner"] != side and g["date"][:4] == year for g in games):
                    out.append("復仇戰：對手今年贏過")
        info["record"] = (wins1, wins2, last)
    return out, info


def _side_name(con, ids: list[int], team: dict | None = None) -> str:
    """名字優先用賽程回應裡的 nameDisplay（明天才出賽的新選手還不在 player 表）；中文名從名單補。"""
    given = {int(p["id"]): p.get("nameDisplay") for p in (team or {}).get("players") or [] if p.get("id")}
    rows = []
    for i in ids:
        db = con.execute("SELECT COALESCE(name_display, slug, player_id), name_zh FROM player WHERE player_id=?",
                         (i,)).fetchone()
        zh_name = db[1] if db else next((r["name_zh"] for r in zh.load_player_table() if r["player_id"] == str(i)), None)
        rows.append((given.get(i) or (db[0] if db else i), zh_name))
    if len(rows) == 2 and all(r[1] for r in rows):
        return "／".join(r[1] for r in rows)
    return " / ".join(zh.player(str(r[0]), r[1]) for r in rows)


def line(con, m: dict, info: dict, why: list[str]) -> str:
    ids1, ids2, r1, r2 = info["ids1"], info["ids2"], info["r1"], info["r2"]
    t1, t2 = m.get("team1"), m.get("team2")
    if info["flip"]:
        ids1, ids2, r1, r2, t1, t2 = ids2, ids1, r2, r1, t2, t1
    n1, n2 = _side_name(con, ids1, t1), _side_name(con, ids2, t2)
    rk = lambda r: f"#{r}" if r else "無排名"
    text = (f"{time_label(m)} {zh.event(info['event'])} {zh.round_name(m.get('roundName'))}："
            f"{n1}（{rk(r1)}）vs {n2}（{rk(r2)}）")
    extra = []
    if "record" in info:
        w1, w2, last = info["record"]
        winner = last["winner"]
        if info["flip"]:
            w1, w2, winner = w2, w1, 3 - winner
        result = "贏" if winner == 1 else "輸"
        extra.append(f"交手 {w1} 勝 {w2} 負，上次 {zh.tournament(last['tournament'])}{zh.round_name(last['round'])}{result}")
    extra += [w for w in why if w not in ("中華台北",)]
    return text + (" — " + "；".join(dict.fromkeys(extra)) if extra else "")


def select(con, schedule: list[dict], on: str, tracked_tpe: set[int]) -> list[str]:
    """賽程 → 看點行（最多 8 場）。只看還沒打、雙方都已確定的個人賽場次。"""
    picks = []
    for m in schedule:
        if m.get("isTeamMatch") or m.get("matchStatus") not in ("N", None) or m.get("winner") in (1, 2):
            continue
        if not _ids(m.get("team1")) or not _ids(m.get("team2")):
            continue
        why, info = reasons(con, m, on, tracked_tpe)
        if why:
            picks.append((m, info, why))
    picks.sort(key=lambda x: (not x[1]["tpe"], (x[1]["r1"] or 999) + (x[1]["r2"] or 999)))
    picks = picks[:MAX_LINES]
    picks.sort(key=lambda x: (taiwan_time(x[0]) or dt.datetime.max, x[0].get("courtName") or ""))
    return [line(con, m, info, why) for m, info, why in picks]
