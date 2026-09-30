"""每站成績：從 match 表推每個組合在每站、每項目「打到第幾輪」與積分（交接單 002 第 3 步）

規則（BWF Statutes 5.3.3.1 第 4.2 條，2018-11-30 版）：
  - 冠軍 = 決賽勝方；其他人 = 輸掉的那一輪
  - 退賽、不戰而敗的一方也算打到該輪
  - 首輪輪空、第二輪就輸 → 拿首輪落敗的積分（4.2.1）
  - 資格賽落敗（假設，待排名驗證確認）：最後一輪資格賽落敗 = 比正賽首輪落敗低一級，每往前一輪再低一級
  - 團體賽的單場不計（規章 7.x 另有公式）

用法：python -m brief.results --db data/brief.db        # 重算全部（冪等）
"""
from __future__ import annotations

import argparse
import datetime as dt
from collections import defaultdict

from brief import ranking_points as rp
from brief.crawler import connect

RESULT_TABLE = """
CREATE TABLE IF NOT EXISTS tournament_result (
    pairing_id      INTEGER NOT NULL REFERENCES pairing(pairing_id),
    tournament_id   INTEGER NOT NULL REFERENCES tournament(tournament_id),
    event           TEXT NOT NULL,
    round_reached   TEXT NOT NULL,                -- ranking_points.POSITIONS：W / F / SF / QF / R16 … R1024
    points          INTEGER,                      -- NULL = 規則未知（例如大英國協運動會）
    rule_version    TEXT NOT NULL,                -- PRE2018 / V2018 / V2024W17
    result_date     TEXT NOT NULL,                -- 該組合在本站最後一場的日期
    PRIMARY KEY (pairing_id, tournament_id, event)
);
CREATE INDEX IF NOT EXISTS ix_result_event_date ON tournament_result(event, result_date);
"""

MAIN_ROUNDS = {"Final": 2, "F": 2, "SF": 4, "QF": 8, "R16": 16, "R32": 32, "R64": 64, "R128": 128,
               "R256": 256, "R512": 512}
POSITION_OF_SIZE = {2: "F", 4: "SF", 8: "QF", 16: "R16", 32: "R32", 64: "R64", 128: "R128",
                    256: "R256", 512: "R512", 1024: "R1024"}
QUAL_PREFIX = "Qual. "
# 舊年度資料的其他寫法（2026-09-30 回補發現）：「Semi-finals」＝四強；「3/4」是奧運銅牌戰，
# 兩邊都已在四強落敗，名次由四強那場決定，所以銅牌戰本身不進籤表；輪次是 NULL 的場次無法判斷，略過
ROUND_ALIAS = {"Semi-finals": "SF", "Quarterfinals": "QF", "Round of 16": "R16", "Round of 32": "R32"}
SKIP_ROUNDS = {"3/4", None, ""}


def _qual_size(rnd: str | None) -> int | None:
    if not rnd or not rnd.startswith(QUAL_PREFIX):
        return None
    return MAIN_ROUNDS.get(rnd[len(QUAL_PREFIX):])


def is_group_round(rnd: str | None, level: str | None) -> bool:
    """小組賽：奧運等的「Group A」；年終總決賽的 R1–R3 是小組循環。"""
    return bool(rnd) and (rnd.startswith("Group") or (level == "WTF" and rnd in ("R1", "R2", "R3")))


def group_positions(group: list[dict], advanced: set[int], first_size: int, level: str | None = None) -> dict[int, str]:
    """規章 4.2.6：小組第 k 名（未晉級）拿「淘汰賽首輪人數 × 2^(k − 每組晉級人數)」那一級的積分。
    例：年終總決賽兩組各 4 人、每組 2 人進四強 → 小組第 3 = 5/8、第 4 = 9/16。
    分組用對戰關係連通判斷；組內依勝場數排名，同勝場看直接交手。"""
    parent: dict[int, int] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for m in group:
        parent[find(m["side1"])] = find(m["side2"])
    members: dict[int, list[int]] = defaultdict(list)
    for x in list(parent):
        members[find(x)].append(x)
    wins = defaultdict(int)
    beat = set()
    for m in group:
        w, l = (m["side1"], m["side2"]) if m["winner_side"] == 1 else (m["side2"], m["side1"])
        wins[w] += 1
        beat.add((w, l))
    out = {}
    for ms in members.values():
        from functools import cmp_to_key

        def cmp(a, b):
            if (a in advanced) != (b in advanced):          # 已晉級的一定在前，未晉級的彼此再比
                return -1 if a in advanced else 1
            if wins[a] != wins[b]:
                return wins[b] - wins[a]
            return -1 if (a, b) in beat else 1 if (b, a) in beat else 0
        ranked = sorted(ms, key=cmp_to_key(cmp))
        adv = sum(x in advanced for x in ms)
        for k, x in enumerate(ranked, 1):
            if x not in advanced and k > adv:
                if level == "WTF" and k in (3, 4):
                    out[x] = f"G{k}"                   # V6.0 §4.2.8：年終總決賽小組第 3、第 4 另有積分（ranking_points）
                else:
                    out[x] = POSITION_OF_SIZE.get(first_size * 2 ** (k - adv), "R1024")
    return out


def event_results(matches: list[dict], level: str | None = None, finished: bool = False) -> dict[int, tuple[str, str]]:
    """一站一項目的所有單場 → {pairing_id: (名次, 最後一場日期)}。
    matches 每筆需有 round、side1、side2、winner_side、match_date。
    finished：整站已結束（API 偶爾缺決賽，此時四強勝方至少給亞軍）。"""
    matches = [{**m, "round": ROUND_ALIAS.get(m["round"], m["round"])} for m in matches if m["round"] not in SKIP_ROUNDS]
    group = [m for m in matches if is_group_round(m["round"], level)]
    matches = [m for m in matches if not is_group_round(m["round"], level)]
    main = [m for m in matches if m["round"] in MAIN_ROUNDS]
    qual = [m for m in matches if _qual_size(m["round"])]
    if not main:
        return {}
    first_size = max(MAIN_ROUNDS[m["round"]] for m in main)
    qual_sizes = sorted({_qual_size(m["round"]) for m in qual})      # 小 → 大：最後一輪資格賽在前

    played: dict[int, list[dict]] = defaultdict(list)
    for m in main + qual:
        played[m["side1"]].append(m)
        played[m["side2"]].append(m)

    out = {}
    for pid, ms in played.items():
        last_date = max(m["match_date"] for m in ms)
        losses = [m for m in ms if (m["side1"] == pid) != (m["winner_side"] == 1)]
        if losses and not any(m["round"] in MAIN_ROUNDS for m in losses) and any(m["round"] in MAIN_ROUNDS for m in ms):
            losses = []                               # 資格賽輸了、以 lucky loser 進正賽且正賽沒輸（例：2024 荷蘭國際賽混雙冠軍）
        if not losses:
            final = [m for m in ms if MAIN_ROUNDS.get(m["round"]) == 2]
            if final:
                out[pid] = ("W", last_date)
            elif finished and any(m["round"] == "SF" for m in ms):
                out[pid] = ("F", last_date)           # 整站已結束但 API 沒有決賽（例：2026 亞錦賽混雙）→ 至少亞軍
            continue                                  # 資料不完整（例如只抓到部分賽程）就不給名次
        # 資格賽輸了又以 lucky loser 進正賽的人，以正賽的敗場為準
        main_losses = [m for m in losses if m["round"] in MAIN_ROUNDS]
        lost = min(main_losses, key=lambda m: MAIN_ROUNDS[m["round"]]) if main_losses else losses[0]
        q = _qual_size(lost["round"])
        if q:
            steps = qual_sizes.index(q) + 1
            out[pid] = (POSITION_OF_SIZE.get(first_size * 2 ** steps, "R1024"), last_date)
            continue
        size = MAIN_ROUNDS[lost["round"]]
        main_played = [m for m in ms if m["round"] in MAIN_ROUNDS]
        if len(main_played) == 1 and size < first_size:
            size = first_size                         # 4.2.1：首輪輪空、第二輪就輸 → 首輪落敗積分
        out[pid] = (POSITION_OF_SIZE[size], last_date)
    if group:
        last = max(m["match_date"] for m in group)
        for pid, pos in group_positions(group, set(played), first_size, level).items():
            out.setdefault(pid, (pos, last))
    return out


def compute(con, tournament_id: int | None = None) -> int:
    """重算 tournament_result（冪等：同一站同一組合只有一筆）。回傳寫入筆數。"""
    con.executescript(RESULT_TABLE)
    where, args = ("AND m.tournament_id = ?", (tournament_id,)) if tournament_id else ("", ())
    rows = con.execute(
        f"""SELECT m.tournament_id, t.name, t.level, t.start_date, t.end_date, m.event, m.round, m.side1_id, m.side2_id,
                   m.winner_side, m.match_date
            FROM match m JOIN tournament t USING (tournament_id)
            WHERE m.team_tie_id IS NULL AND m.winner_side IN (1, 2) {where}""", args).fetchall()
    groups: dict[tuple, list[dict]] = defaultdict(list)
    meta = {}
    last_day: dict[int, str] = {}
    for tid, name, level, start, end, event, rnd, s1, s2, win, mdate in rows:
        groups[(tid, event)].append({"round": rnd, "side1": s1, "side2": s2, "winner_side": win,
                                     "match_date": mdate})
        meta[tid] = (name, level, start, end)
        last_day[tid] = max(last_day.get(tid, ""), mdate)
    n = 0
    for (tid, event), ms in groups.items():
        name, level, start, end = meta[tid]
        on = dt.date.fromisoformat(start)
        version = rp.version(on)[0]
        finished = bool(end) and last_day[tid] >= end
        # 成績日期用整站最後一天：官方在整站結束後才計入排名（早早出局的人不能提早算進去）
        done_day = last_day[tid]
        for pid, (pos, _) in event_results(ms, level, finished).items():
            last = done_day
            pts = rp.points(on, level, pos, name)
            con.execute(
                """INSERT INTO tournament_result (pairing_id, tournament_id, event, round_reached, points,
                                                  rule_version, result_date)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(pairing_id, tournament_id, event) DO UPDATE SET
                     round_reached=excluded.round_reached, points=excluded.points,
                     rule_version=excluded.rule_version, result_date=excluded.result_date""",
                (pid, tid, event, pos, pts, version, last))
            n += 1
    con.commit()
    return n + compute_team(con, tournament_id)


def main():
    ap = argparse.ArgumentParser(description="每站成績與積分")
    ap.add_argument("--db", default="brief.db")
    ap.add_argument("--tournament-id", type=int)
    a = ap.parse_args()
    print(f"寫入 {compute(connect(a.db), a.tournament_id)} 筆")


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------- 團體賽（規章 7.x）
TEAM_LEVELS = ("G1_TEAM", "MULTI_TEAM", "CONT_TEAM")
TEAM_POSITION = "TEAM"


def _standing(con, pairing: int, event: str, before: str) -> tuple[float, int] | None:
    """before（含）之前最近一週的（積分, 計入站數）：先查官方快照，沒有再查估算。"""
    for table in ("ranking_snapshot", "ranking_estimate"):
        if not con.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
            continue
        week = con.execute(f"SELECT MAX(week_date) FROM {table} WHERE event=? AND week_date <= ?", (event, before)).fetchone()[0]
        if not week:
            continue
        row = con.execute(f"SELECT points, tournaments FROM {table} WHERE week_date=? AND event=? AND pairing_id=?",
                          (week, event, pairing)).fetchone()
        if row and row[0]:
            return float(row[0]), int(row[1] or 1)
        if table == "ranking_snapshot":
            return None                      # 官方快照涵蓋這段時間，查不到就是沒有排名
    return None


def _best_with_other_partner(con, player: int, pairing: int, event: str, before: str) -> float | None:
    """5.3.3.4 §1.2：選手與其他搭檔的最高排名積分 ÷ 站數。"""
    best = None
    for other, in con.execute("SELECT pairing_id FROM pairing WHERE ? IN (player_a_id, player_b_id) AND pairing_id != ? "
                              "AND player_b_id IS NOT NULL", (player, pairing)):
        st = _standing(con, other, event, before)
        if st and (best is None or st[0] > best[0]):
            best = st
    return best[0] / best[1] if best else None


def team_basis(con, pairing: int, event: str, before: str, v6: bool) -> tuple[float, float] | None:
    """回傳（輸球時自己拿的分數, 給對手計算用的總積分）；完全沒有排名回傳 None。
    V6.0 §7.2.1–7.2.6、5.3.3.4：
      一般：平均分 = 積分 / min(站數, 10)，總積分 = 排名積分
      雙打同組 < 8 站：調整排名 = 積分 × 10 ÷ max(站數, 5)，自己拿 調整 ÷ 10
      雙打沒有同組排名：名目排名 = 兩人與其他搭檔的最佳（積分 ÷ 站數）平均 × 10 × 80%，自己拿 名目 ÷ 10"""
    st = _standing(con, pairing, event, before)
    doubles = event in ("MD", "WD", "XD")
    if st:
        pts, n = st
        if v6 and doubles and n < 8:
            adjusted = pts * 10 / max(n, 5)
            return adjusted / 10, adjusted
        return pts / min(n, 10), pts
    if v6 and doubles:
        a, b = con.execute("SELECT player_a_id, player_b_id FROM pairing WHERE pairing_id=?", (pairing,)).fetchone()
        pa = _best_with_other_partner(con, a, pairing, event, before)
        pb = _best_with_other_partner(con, b, pairing, event, before) if b else None
        if pa is not None and pb is not None:
            notional = (pa + pb) / 2 * 10 * 0.8
            return notional / 10, notional
    return None


def team_match_points(own: tuple[float, float] | None, opp: tuple[float, float] | None, won: bool,
                      v6: bool = True) -> float:
    """一場團體賽單場的積分。own / opp 為 team_basis 的結果。
    有排名：贏 = 自己的基準 + 對手總積分 / 100；輸 = 自己的基準（§7.2.1–7.2.6）
    沒有排名：贏 = 對手 / 100（2018 版另加 1 分；V6.0 §7.2.7 沒有），對手也沒排名 = 2；輸 = 0"""
    if own is None:
        if not won:
            return 0.0
        if opp is None:
            return 2.0
        return opp[1] / 100 + (0.0 if v6 else 1.0)
    return own[0] + (opp[1] / 100 if won and opp else 0.0)


def compute_team(con, tournament_id: int | None = None) -> int:
    """團體賽每個組合在一站的成績 = 該站單場最高分（實測與官方排名吻合，見 docs/ranking-validation.md）。"""
    con.executescript(RESULT_TABLE)
    where, args = ("AND m.tournament_id = ?", (tournament_id,)) if tournament_id else ("", ())
    rows = con.execute(
        f"""SELECT m.tournament_id, t.start_date, m.event, m.side1_id, m.side2_id, m.winner_side, m.match_date
            FROM match m JOIN tournament t USING (tournament_id)
            WHERE m.team_tie_id IS NOT NULL AND m.winner_side IN (1, 2)
              AND t.level IN ({",".join("?" * len(TEAM_LEVELS))}) {where}""", (*TEAM_LEVELS, *args)).fetchall()
    best: dict[tuple, float] = {}
    last_day: dict[int, str] = {}
    start_of: dict[int, str] = {}
    for tid, start, event, s1, s2, win, mdate in rows:
        last_day[tid] = max(last_day.get(tid, ""), mdate)
        start_of[tid] = start
    cache: dict[tuple, tuple | None] = {}
    for tid, start, event, s1, s2, win, mdate in rows:
        v6 = rp.version(dt.date.fromisoformat(start))[0] == "V2024W17"
        # V6.0 §7.4：用「決賽日所在排名週的前一週」；更早的版本沒寫，一併採用
        base = (dt.date.fromisoformat(last_day[tid]) - dt.timedelta(days=7)).isoformat()
        for pid in (s1, s2):
            if (pid, event, base) not in cache:
                cache[(pid, event, base)] = team_basis(con, pid, event, base, v6)
        for me, opp, won in ((s1, s2, win == 1), (s2, s1, win == 2)):
            v = team_match_points(cache[(me, event, base)], cache[(opp, event, base)], won, v6)
            key = (me, tid, event)
            best[key] = max(best.get(key, 0.0), v)
    for (pid, tid, event), v in best.items():
        con.execute(
            """INSERT INTO tournament_result (pairing_id, tournament_id, event, round_reached, points, rule_version, result_date)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(pairing_id, tournament_id, event) DO UPDATE SET
                 round_reached=excluded.round_reached, points=excluded.points, result_date=excluded.result_date""",
            (pid, tid, event, TEAM_POSITION, round(v), rp.version(dt.date.fromisoformat(start_of[tid]))[0], last_day[tid]))
    con.commit()
    return len(best)
