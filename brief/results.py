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
ROUND_ALIAS = {"Semi-finals": "SF"}
SKIP_ROUNDS = {"3/4", None, ""}


def _qual_size(rnd: str | None) -> int | None:
    if not rnd or not rnd.startswith(QUAL_PREFIX):
        return None
    return MAIN_ROUNDS.get(rnd[len(QUAL_PREFIX):])


def event_results(matches: list[dict]) -> dict[int, tuple[str, str]]:
    """一站一項目的所有單場 → {pairing_id: (名次, 最後一場日期)}。
    matches 每筆需有 round、side1、side2、winner_side、match_date。"""
    matches = [{**m, "round": ROUND_ALIAS.get(m["round"], m["round"])} for m in matches if m["round"] not in SKIP_ROUNDS]
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
        if not losses:
            final = [m for m in ms if MAIN_ROUNDS.get(m["round"]) == 2]
            if final:
                out[pid] = ("W", last_date)
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
    return out


def compute(con, tournament_id: int | None = None) -> int:
    """重算 tournament_result（冪等：同一站同一組合只有一筆）。回傳寫入筆數。"""
    con.executescript(RESULT_TABLE)
    where, args = ("AND m.tournament_id = ?", (tournament_id,)) if tournament_id else ("", ())
    rows = con.execute(
        f"""SELECT m.tournament_id, t.name, t.level, t.start_date, m.event, m.round, m.side1_id, m.side2_id,
                   m.winner_side, m.match_date
            FROM match m JOIN tournament t USING (tournament_id)
            WHERE m.team_tie_id IS NULL AND m.winner_side IN (1, 2) {where}""", args).fetchall()
    groups: dict[tuple, list[dict]] = defaultdict(list)
    meta = {}
    for tid, name, level, start, event, rnd, s1, s2, win, mdate in rows:
        groups[(tid, event)].append({"round": rnd, "side1": s1, "side2": s2, "winner_side": win,
                                     "match_date": mdate})
        meta[tid] = (name, level, start)
    n = 0
    for (tid, event), ms in groups.items():
        name, level, start = meta[tid]
        on = dt.date.fromisoformat(start)
        version = rp.version(on)[0]
        for pid, (pos, last) in event_results(ms).items():
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
    return n


def main():
    ap = argparse.ArgumentParser(description="每站成績與積分")
    ap.add_argument("--db", default="brief.db")
    ap.add_argument("--tournament-id", type=int)
    a = ap.parse_args()
    print(f"寫入 {compute(connect(a.db), a.tournament_id)} 筆")


if __name__ == "__main__":
    main()
