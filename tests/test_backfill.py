"""十年回補測試：用 North Harbour 與湯尤盃的真實回應，不連網。"""
import datetime as dt
import json
from pathlib import Path

from brief import backfill, crawler

FIXDIR = Path(__file__).parent / "fixtures"
TODAY = dt.date(2026, 10, 10)
NH = "1B95960C-1C1B-41E2-B7CF-120E8CA38CE3"


def load(name):
    return json.loads((FIXDIR / name).read_text(encoding="utf-8"))


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self):
        self.calls = []

    def get(self, url, **params):
        assert url.endswith("day-matches"), url
        self.calls.append((params["tournamentCode"], params["date"]))
        if params["tournamentCode"] == NH and params["date"] == "2026-09-30":
            return FakeResponse(load("north_harbour_2026-09-30_sample.json"))
        if params["tournamentCode"] == "UBER" and params["date"] == "2026-05-02":
            return FakeResponse(load("uber_cup_2026_sf_chn_jpn.json"))
        return FakeResponse([])


def db():
    con = crawler.connect(":memory:")
    rows = [
        (5766, NH, "MAXX North Harbour International 2026", "IC", "normal", "2026-09-30", "2026-10-01"),
        (5600, "UBER", "BWF Thomas & Uber Cup Finals 2026", "G1_TEAM", "normal", "2026-05-01", "2026-05-03"),
        (4000, "EMPTY", "Old Challenge 2019", "IC", "normal", "2019-03-01", "2019-03-02"),
        (4001, None, "No GUID International 2018", "IS", "normal", "2018-05-01", "2018-05-01"),
        (4002, "CXL", "Cancelled Series 2020", "IS", "cancelled", "2020-04-01", "2020-04-03"),
        (5900, "FUTURE", "Future Open 2026", "S300", "normal", "2026-11-01", "2026-11-05"),
    ]
    for tid, code, name, level, status, start, end in rows:
        crawler.upsert_tournament(con, {"tournament_id": tid, "code": code, "name": name, "level": level,
                                        "status": status, "start_date": start, "end_date": end, "source_url": ""})
    con.commit()
    return con


def test_candidates_newest_first_and_skip_cancelled_or_future():
    got = [t["tournament_id"] for t in backfill.candidates(db(), TODAY)]
    assert got == [5766, 5600, 4000, 4001]


def test_run_records_status_and_is_resumable():
    con = db()
    client = FakeClient()
    res = backfill.run(con, client, TODAY, verbose=False)
    assert res == {"done": 2, "empty": 1, "failed": 1, "matches": 6 + 3}   # North Harbour 節錄 8 場中已完成 6 場；湯尤盃四強 3 點
    status = dict(con.execute("SELECT tournament_id, status FROM backfill_status"))
    assert status == {5766: "done", 5600: "done", 4000: "empty", 4001: "failed"}
    assert len(client.calls) == 2 + 3 + 2                     # 每站每天一個請求；缺 GUID 的站不發請求

    counts = [con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("match", "game", "team_tie")]
    again = FakeClient()
    assert backfill.run(con, again, TODAY, verbose=False) == {}   # 續跑：全部跳過
    assert again.calls == []
    assert [con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("match", "game", "team_tie")] == counts


def test_retry_failed_and_rerun_is_idempotent():
    con = db()
    backfill.run(con, FakeClient(), TODAY, verbose=False)
    con.execute("UPDATE tournament SET code='FIXED' WHERE tournament_id=4001")
    res = backfill.run(con, FakeClient(), TODAY, retry_failed=True, verbose=False)
    assert res == {"empty": 1, "matches": 0}
    assert con.execute("SELECT status, error FROM backfill_status WHERE tournament_id=4001").fetchone() == ("empty", None)

    # 把已完成的站清掉狀態再跑一次：比賽只更新、不重複
    before = con.execute("SELECT COUNT(*) FROM match").fetchone()[0]
    con.execute("DELETE FROM backfill_status")
    backfill.run(con, FakeClient(), TODAY, verbose=False)
    assert con.execute("SELECT COUNT(*) FROM match").fetchone()[0] == before


def test_year_limit_and_report():
    con = db()
    assert backfill.run(con, FakeClient(), TODAY, year=2026, limit=1, verbose=False)["done"] == 1
    text = backfill.report(con)
    assert "| 2026 | IC | 1 | 1 | 0 | 0 | 6 |" in text
    assert "| 2020 |" not in text                             # 取消的賽事不列
