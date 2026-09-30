"""每站成績測試：規則用人造籤表，寫入與冪等用真實 fixture。"""
import json
from pathlib import Path

from brief import crawler, results

FIXDIR = Path(__file__).parent / "fixtures"


def m(rnd, a, b, winner=1, date="2019-09-10"):
    return {"round": rnd, "side1": a, "side2": b, "winner_side": winner, "match_date": date}


def bracket():
    """8 人正賽（1 號種子輪空）+ 一輪資格賽。"""
    return [
        m("Qual. R16", 20, 21), m("Qual. R16", 22, 23),        # 20、22 晉級正賽
        m("QF", 2, 3), m("QF", 4, 20), m("QF", 5, 22, winner=2),
        m("SF", 1, 2, date="2019-09-12"), m("SF", 4, 22, date="2019-09-12"),
        m("Final", 1, 4, winner=2, date="2019-09-13"),
    ]


def test_positions_from_bracket():
    got = results.event_results(bracket())
    assert got[4] == ("W", "2019-09-13")
    assert got[1] == ("F", "2019-09-13")
    assert got[2][0] == "SF" and got[22][0] == "SF"
    assert got[3][0] == "QF" and got[20][0] == "QF" and got[5][0] == "QF"
    assert got[21][0] == "R16" and got[23][0] == "R16"        # 資格賽最後一輪落敗 = 正賽首輪下一級


def test_bye_then_lose_gets_first_round_points():
    ms = [m("R16", 1, 2), m("R32", 2, 9), m("R32", 3, 10)]    # 1 輪空、16 強就輸
    ms[0]["winner_side"] = 2
    assert results.event_results(ms)[1][0] == "R32"


def test_lucky_loser_uses_main_draw_loss():
    ms = [m("Qual. R16", 30, 31), m("QF", 2, 31), m("SF", 2, 3)]   # 31 資格賽輸、遞補正賽打進八強
    assert results.event_results(ms)[31][0] == "QF"


def test_earlier_qual_round_is_one_step_lower():
    ms = [m("Qual. R32", 40, 41), m("Qual. R16", 40, 42), m("QF", 40, 1), m("SF", 40, 2), m("Final", 40, 3)]
    got = results.event_results(ms)
    assert got[42][0] == "R16" and got[41][0] == "R32"


def test_compute_real_fixture_and_rerun_is_idempotent():
    con = crawler.connect(":memory:")
    crawler.upsert_tournament(con, {"tournament_id": 5766, "code": "X", "name": "MAXX North Harbour International 2026",
                                    "level": "IC", "start_date": "2026-09-30", "end_date": "2026-10-04",
                                    "source_url": ""})
    crawler.store_day(con, 5766, json.loads((FIXDIR / "north_harbour_2026-09-30_sample.json").read_text(encoding="utf-8")))
    n = results.compute(con)
    assert n == results.compute(con) and n > 0
    assert con.execute("SELECT COUNT(*) FROM tournament_result").fetchone()[0] == n
    rows = con.execute("SELECT DISTINCT rule_version FROM tournament_result").fetchall()
    assert rows == [("V2024W17",)]
    # 首日只有第一輪，輸家拿首輪落敗積分，贏家還沒有名次
    pts = {r for (r,) in con.execute("SELECT points FROM tournament_result")}
    assert pts <= {360, 920, 170}


def test_old_round_names_and_null_rounds():
    """2016–2021 資料：Semi-finals、奧運銅牌戰 3/4、輪次為 NULL（2026-09-30 回補發現）。"""
    ms = [m("QF", 1, 5), m("QF", 2, 6), m("QF", 3, 7), m("QF", 4, 8),
          m("Semi-finals", 1, 2), m("Semi-finals", 3, 4), m("3/4", 2, 4), m(None, 9, 10), m("Final", 1, 3)]
    got = results.event_results(ms)
    assert got[1][0] == "W" and got[3][0] == "F"
    assert got[2][0] == "SF" and got[4][0] == "SF"          # 銅牌戰勝負不改變四強名次
    assert 9 not in got and 10 not in got                   # 輪次不明的場次略過


def test_wtf_group_stage_positions():
    """規章 4.2.6：年終總決賽兩組各 4 人、每組前 2 進四強 → 小組第 3 = 5/8（QF）、第 4 = 9/16（R16）。"""
    g = lambda a, b, w=1: m("R1", a, b, winner=w)
    group_a = [g(1, 2), g(1, 3), g(1, 4), g(2, 3), g(2, 4), g(3, 4)]          # 1 三勝、2 兩勝、3 一勝、4 零勝
    group_b = [g(5, 6), g(5, 7), g(5, 8), g(6, 7), g(6, 8), g(7, 8)]
    ko = [m("SF", 1, 6), m("SF", 5, 2), m("Final", 1, 5)]
    got = results.event_results(group_a + group_b + ko, level="WTF")
    assert got[1][0] == "W" and got[5][0] == "F" and got[2][0] == "SF" and got[6][0] == "SF"
    assert (got[3][0], got[4][0], got[7][0], got[8][0]) == ("QF", "R16", "QF", "R16")
    assert 3 not in results.event_results(group_a + ko)                      # 不是 WTF 時 R1 不當小組賽


def test_group_tie_broken_by_head_to_head():
    g = lambda a, b: m("Group A", a, b)
    ms = [g(1, 2), g(2, 3), g(3, 1), g(1, 4), g(2, 4), g(3, 4), m("QF", 1, 9), m("SF", 9, 10), m("Final", 9, 11)]
    # 1、2、3 都是 2 勝；只有 1 晉級 → 2 與 3 以直接交手（2 勝 3）排名
    got = results.event_results(ms)
    assert got[2][0] == "R16" and got[3][0] == "R32" and got[4][0] == "R64"


def test_missing_final_gives_runner_up_only_when_finished():
    ms = [m("SF", 1, 2), m("SF", 3, 4)]                                       # 決賽不在 API
    assert 1 not in results.event_results(ms)
    assert results.event_results(ms, finished=True)[1][0] == "F"


def test_result_date_is_tournament_last_day():
    con = crawler.connect(":memory:")
    crawler.upsert_tournament(con, {"tournament_id": 1, "code": "X", "name": "T", "level": "S300",
                                    "start_date": "2026-01-01", "end_date": "2026-01-03", "source_url": ""})
    for pid in (1, 2, 3, 4):
        con.execute("INSERT INTO player (player_id) VALUES (?)", (pid,))
        con.execute("INSERT INTO pairing (pairing_id, player_a_id) VALUES (?, ?)", (pid, pid))
    for mid, rnd, a, b, d in [(1, "SF", 1, 2, "2026-01-02"), (2, "SF", 3, 4, "2026-01-02"), (3, "Final", 1, 3, "2026-01-03")]:
        con.execute("INSERT INTO match (match_id, tournament_id, event, round, match_date, side1_id, side2_id, winner_side) "
                    "VALUES (?, 1, 'MS', ?, ?, ?, ?, 1)", (mid, rnd, d, a, b))
    results.compute(con)
    assert {r[0] for r in con.execute("SELECT result_date FROM tournament_result")} == {"2026-01-03"}   # 四強出局者也用整站最後一天
