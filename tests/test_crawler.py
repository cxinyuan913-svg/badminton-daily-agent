"""爬蟲原型測試：用 2026-09-30 North Harbour International 的真實 API 回應（節錄 8 場）。
執行：python -m pytest -q
"""
import json
import sqlite3
from pathlib import Path

from brief import crawler

FIX = Path(__file__).parent / "fixtures" / "north_harbour_2026-09-30_sample.json"


def load_db():
    con = crawler.connect(":memory:")
    crawler.upsert_tournament(con, {"tournament_id": 5766, "code": "1B95960C-1C1B-41E2-B7CF-120E8CA38CE3",
                                    "name": "MAXX North Harbour International 2026", "start_date": "2026-09-30",
                                    "end_date": "2026-10-04", "source_url": ""})
    stats = crawler.store_day(con, 5766, json.loads(FIX.read_text(encoding="utf-8")))
    con.commit()
    return con, stats


def test_only_finished_matches_stored():
    con, stats = load_db()
    assert stats["stored"] == 6            # 5 場 Finished + 1 場 Off court
    assert stats["skipped_unfinished"] == 2  # On Court、尚未開打


def test_scores_and_winner():
    con, _ = load_db()
    games = con.execute("SELECT game_no, side1_points, side2_points FROM game WHERE match_id=1551289 ORDER BY game_no").fetchall()
    assert games == [(1, 12, 21), (2, 12, 21)]
    assert con.execute("SELECT winner_side FROM match WHERE match_id=1551289").fetchone()[0] == 2


def test_rerun_is_idempotent():
    con, _ = load_db()
    crawler.store_day(con, 5766, json.loads(FIX.read_text(encoding="utf-8")))
    assert con.execute("SELECT COUNT(*) FROM match").fetchone()[0] == 6
    assert con.execute("SELECT COUNT(*) FROM game").fetchone()[0] == 12


def test_player_career_across_partners():
    """CHUNG 當天打了男單和混雙：個人生涯要同時看到兩場，混雙要帶出搭檔。"""
    con, _ = load_db()
    rows = con.execute("SELECT event, partner_id, won FROM player_match WHERE player_id=59967 ORDER BY event").fetchall()
    assert rows == [("MS", None, 0), ("XD", 26710, 1)]


def test_doubles_pairing_is_order_independent():
    con, _ = load_db()
    a = crawler.pairing_id(con, [26710, 59967])
    b = crawler.pairing_id(con, [59967, 26710])
    assert a == b


def test_date_range_parsing():
    assert crawler.parse_date_range("21 - 26 July", 2026) == (
        __import__("datetime").date(2026, 7, 21), __import__("datetime").date(2026, 7, 26))
    s, e = crawler.parse_date_range("30 September - 04 October", 2026)
    assert (s.isoformat(), e.isoformat()) == ("2026-09-30", "2026-10-04")
    s, e = crawler.parse_date_range("29 December - 03 January", 2026)
    assert e.isoformat() == "2027-01-03"


def test_parse_tournament_page():
    page = ('<title>Tournament | MAXX North Harbour International 2026</title>'
            '<div class="live-date">30 September - 04 October</div>'
            '<x data-code="1b95960c-1c1b-41e2-b7cf-120e8ca38ce3">')
    t = crawler.parse_tournament_page(5766, page)
    assert t["name"] == "MAXX North Harbour International 2026"
    assert t["code"] == "1B95960C-1C1B-41E2-B7CF-120E8CA38CE3"
    assert (t["start_date"], t["end_date"]) == ("2026-09-30", "2026-10-04")


def test_historical_null_status_and_retirement():
    """2019 Myanmar International Series：舊賽事 matchStatus 是 null；R16 有一場退賽。"""
    con, _ = load_db()
    base = {"isTeamMatch": False, "matchStatus": None, "courtName": "1", "duration": 30,
            "team1seed": None, "team2seed": None, "matchTimeUtc": None}
    p = lambda i, f, l, c: {"id": str(i), "firstName": f, "lastName": l, "nameDisplay": f"{f} {l}",
                            "nameShort": l, "countryCode": c, "slug": l.lower()}
    final = {**base, "id": 900001, "eventName": "MS", "roundName": "Final", "matchTime": "2019-09-15 14:00:00",
             "winner": 2, "scoreStatusValue": "Normal",
             "team1": {"players": [p(1, "Karono", "KARONO", "INA")]},
             "team2": {"players": [p(82670, "Kaushal", "DHARMAMER", "IND")]},
             "score": [{"set": 1, "home": 21, "away": 18}, {"set": 2, "home": 14, "away": 21}, {"set": 3, "home": 11, "away": 21}]}
    retired = {**base, "id": 900002, "eventName": "WS", "roundName": "R16", "matchTime": "2019-09-12 10:00:00",
               "winner": 2, "scoreStatusValue": "Retired",
               "team1": {"players": [p(2, "A", "PLAYER", "MYA")]}, "team2": {"players": [p(3, "B", "PLAYER", "THA")]},
               "score": [{"set": 1, "home": 13, "away": 21}]}
    stats = crawler.store_day(con, 5766, [final, retired])
    assert stats["stored"] == 2
    assert con.execute("SELECT score_status FROM match WHERE match_id=900002").fetchone()[0] == "Retired"
    assert con.execute("SELECT COUNT(*) FROM game WHERE match_id=900001").fetchone()[0] == 3
