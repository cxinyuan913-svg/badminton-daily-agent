"""故事候選資料層（notes 15:40）：純函式用造的交手紀錄，其餘用本機十年資料庫（沒有就略過）。"""
from pathlib import Path

import pytest

from brief import storylines as sl


def ms(seq):
    return [{"a_won": c == "W", "round": "QF", "games": [], "duration": None, "tournament": "x", "date": "2026-01-01"} for c in seq]


def test_turning_point_and_streak():
    k, gap = sl.turning_point(ms("LLLWLLLWWWWWWWWW"))
    assert k == 7 and gap > 0.7                      # 前 7 場 1 勝 6 負 → 之後 9 連勝
    assert sl.turning_point(ms("WLWLWLWLWL")) is None   # 沒有翻轉
    assert sl.streak(ms("LLWWW")) == (True, 3)
    assert sl.streak(ms("WWL")) == (False, 1)


DB = Path(__file__).resolve().parent.parent / "data" / "brief.db"
needs_db = pytest.mark.skipif(not DB.exists(), reason="需要本機的十年回補資料庫")


def _t(con, tid):
    return dict(zip(["tournament_id", "name", "level", "start_date", "end_date"], con.execute(
        "SELECT tournament_id, name, level, start_date, end_date FROM tournament WHERE tournament_id=?", (tid,)).fetchone()))


@needs_db
def test_rivalry_an_yamaguchi_asian_games():
    from brief.crawler import connect
    con = connect(str(DB))
    t = _t(con, 5874)
    allm = sl.tournament_matches(con, 5874, t["end_date"])
    final = [m for m in allm if m["event"] == "WS" and m["round"] == "Final"][0]
    riv = [c for c in sl.candidates_for_match(con, final) if c["kind"] == "rivalry"][0]
    text = "\n".join(riv["facts"])
    assert "23 勝 15 負" in text and "連勝 9 場" in text and "2017 年以來" in text
    assert "9 勝 0 負" in text                         # 翻轉點之後全勝


@needs_db
def test_retired_final_denmark_2021():
    """2021 丹麥公開賽女單決賽，安洗瑩退賽、山口茜奪冠。"""
    from brief.crawler import connect
    con = connect(str(DB))
    t = _t(con, 3971)
    allm = sl.tournament_matches(con, 3971, t["end_date"])
    final = [m for m in allm if m["event"] == "WS" and m["round"] == "Final"][0]
    ret = [c for c in sl.candidates_for_match(con, final) if c["kind"] == "retired"]
    assert ret and "中途退賽" in ret[0]["facts"][1] and "AN Se Young" in ret[0]["facts"][1]


@needs_db
def test_candidates_only_use_database_facts():
    """每則候選的第一條都是那場比賽本身；排名只有官方（沒有「估算」字樣）。"""
    from brief.crawler import connect
    con = connect(str(DB))
    t = _t(con, 5601)
    allm = sl.tournament_matches(con, 5601, t["end_date"])
    cs = sl.story_candidates(con, t, allm, allm)
    assert {c["kind"] for c in cs} >= {"rivalry", "stuck_round", "record"}
    assert all("估算" not in f for c in cs for f in c["facts"])
