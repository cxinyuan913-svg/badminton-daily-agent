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


def test_paraguay_is_not_para_badminton():
    """「para」要比對整個字：Paraguay International Series 2023 曾被誤排除。"""
    fix = json.loads((Path(__file__).parent / "fixtures" / "calendar_2023_paraguay.json").read_text(encoding="utf-8"))
    rows = {r["tournament_id"]: r["level"] for r in calendar.parse_year(fix)}
    assert rows == {4947: "IS"}
    para = {"id": 1, "name": "Para Badminton World Championships 2024", "category": "International Series"}
    assert calendar.classify(para) is None


def test_future_series_and_continental_team():
    """2026-09-30：洲際團體錦標賽納入（22:35）；Future Series 不收（23:10 取消回補）。"""
    fix = json.loads((Path(__file__).parent / "fixtures" / "calendar_2025_fs_cont_team.json").read_text(encoding="utf-8"))
    raw = [t for m in fix["results"] for t in m["tournaments"]]
    rows = {r["tournament_id"]: r["level"] for r in calendar.parse_year(fix)}
    for t in raw:
        cat = " ".join(t["category"].split())
        if cat == "Continental Team Championships":
            assert rows[t["id"]] == "CONT_TEAM"
        else:
            assert t["id"] not in rows                            # Future Series、青少年團體賽不收


def test_fisu_university_games_tracked():
    """22:45 決議：世大運（V6.0 §7.1 計入排名）納入追蹤，level = FISU；元老賽照樣排除。"""
    fix = json.loads((Path(__file__).parent / "fixtures" / "calendar_2025_fisu.json").read_text(encoding="utf-8"))
    raw = [t for m in fix["results"] for t in m["tournaments"]]
    rows = {r["tournament_id"]: r["level"] for r in calendar.parse_year(fix)}
    for t in raw:
        if "FISU" in t["name"]:
            assert rows[t["id"]] == "FISU"
        else:
            assert t["id"] not in rows
    assert calendar.classify({"id": 1, "name": "Chengdu 2021 FISU World University Games (INDIVIDUAL))",
                              "category": "Grade 1 – Team Tournaments"})["level"] == "FISU"
