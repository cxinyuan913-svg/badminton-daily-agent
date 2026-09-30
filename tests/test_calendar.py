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
    assert 5874 not in rows                      # 亞運：待決定，預設不收


def test_optional_categories():
    rows = {r["tournament_id"]: r for r in calendar.parse_year(FIX, include_optional=True)}
    assert rows[5874]["level"] == "MULTI"


def test_rows_feed_crawler_directly():
    """calendar 的輸出可以直接交給 crawler.crawl_known（有 GUID 與日期）。"""
    row = next(r for r in calendar.parse_year(FIX) if r["tournament_id"] == 5766)
    assert row["code"] == "1B95960C-1C1B-41E2-B7CF-120E8CA38CE3"
    assert (row["start_date"], row["end_date"]) == ("2026-09-30", "2026-10-04")
    con = crawler.connect(":memory:")
    crawler.upsert_tournament(con, row)
    assert con.execute("SELECT grade, level FROM tournament WHERE tournament_id=5766").fetchone() == (3, "IC")
