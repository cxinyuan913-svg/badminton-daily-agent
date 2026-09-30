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
