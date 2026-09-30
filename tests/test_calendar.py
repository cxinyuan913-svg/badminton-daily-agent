import json
from pathlib import Path

from brief import calendar, crawler

FIX = json.loads((Path(__file__).parent / "fixtures" / "calendar_2026_sample.json").read_text(encoding="utf-8"))


def test_levels_from_real_calendar():
    rows = {r["tournament_id"]: r for r in calendar.parse_year(FIX)}
    assert rows[5766]["level"] == "IC"          # North Harbour International 2026
    assert rows[5622]["level"] == "S1000"       # China Open 2026
    assert rows[5601]["level"] == "G1_IND"      # 世錦賽
    assert rows[5600]["level"] == "G1_TEAM"     # 湯尤盃
    assert rows[5602]["level"] == "WTF"
    assert 5797 not in rows                      # 青少年團體賽
    assert rows[5874]["level"] == "MULTI"        # 2026 亞運個人賽（2026-09-30 決議納入）


def test_multi_sport_name_fallback():
    """2018 亞運被歸在 Other、2022 亞運團體賽在 Continental Team Games，用名稱補抓。"""
    base = {"start_date": "2018-08-19 00:00:00", "end_date": "2018-08-28 00:00:00", "code": "X", "status": {"code": "normal"}}
    payload = {"results": [{"tournaments": [
        {**base, "id": 3400, "name": "Asian Games 2018 ( Individual Event)", "category": "Other"},
        {**base, "id": 4994, "name": "ASIAN Games 2022 (Team Event) - Non World Ranking", "category": "Continental Team Games"},
        {**base, "id": 3256, "name": "Youth Olympic Games 2018", "category": "Other"},
        {**base, "id": 2501, "name": "Badminton Asia Championships 2016", "category": "Continental Individual Championships"},
    ]}]}
    rows = {r["tournament_id"]: r["level"] for r in calendar.parse_year(payload)}
    assert rows == {3400: "MULTI", 4994: "MULTI_TEAM", 2501: "CONT_IND"}


def test_rows_feed_crawler_directly():
    """calendar 的輸出可以直接交給 crawler.crawl_known（有 GUID 與日期）。"""
    row = next(r for r in calendar.parse_year(FIX) if r["tournament_id"] == 5766)
    assert row["code"] == "1B95960C-1C1B-41E2-B7CF-120E8CA38CE3"
    assert (row["start_date"], row["end_date"]) == ("2026-09-30", "2026-10-04")
    con = crawler.connect(":memory:")
    crawler.upsert_tournament(con, row)
    assert con.execute("SELECT grade, level FROM tournament WHERE tournament_id=5766").fetchone() == (3, "IC")
