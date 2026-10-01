"""每日收集測試：用 2026-09-30 的真實賽程、進行中賽事、賽果與排名回應，不連網。"""
import datetime as dt
import json
from pathlib import Path

from brief import calendar, crawler, daily, live, news

FIXDIR = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXDIR / name).read_text(encoding="utf-8"))


CAL = calendar.parse_year(load("calendar_2026_sample.json"))
LIVE = live.parse_live(load("current_live_2026-09-30.json"))
D = dt.date.fromisoformat


def test_days_to_crawl_window():
    nh = next(r for r in CAL if r["tournament_id"] == 5766)       # 9/30–10/4
    assert daily.days_to_crawl(nh, D("2026-10-01")) == ["2026-09-30", "2026-10-01"]
    assert daily.days_to_crawl(nh, D("2026-10-06")) == ["2026-10-04"]   # 最後一天隔兩天還補得到
    assert daily.days_to_crawl(nh, D("2026-10-07")) == []
    assert daily.days_to_crawl(nh, D("2026-09-29")) == []
    assert daily.days_to_crawl(nh, D("2026-10-04"), lookback=None) == [
        "2026-09-30", "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"]


def test_select_targets_on_2026_09_30():
    got = {t["tournament_id"]: days for t, days in daily.select_targets(CAL, LIVE, D("2026-09-30"), crawled={5874})}
    # 亞運個人賽 9/29 結束，已抓過 → 只補最近兩天；North Harbour 沒抓過 → 從開賽日；非洲青少年團體賽不在範圍
    assert got == {5874: ["2026-09-28", "2026-09-29"], 5766: ["2026-09-30"]}


def test_first_time_tournament_is_backfilled_from_start():
    got = dict((t["tournament_id"], days) for t, days in daily.select_targets(CAL, [], D("2026-09-30")))
    assert got[5874] == ["2026-09-25", "2026-09-26", "2026-09-27", "2026-09-28", "2026-09-29"]


def test_team_event_is_tracked_while_live():
    """湯尤盃進行中時 live 的名稱篩選會排除它（含 team），範圍要以賽程為準。"""
    got = [t["tournament_id"] for t, _ in daily.select_targets(CAL, [], D("2026-04-28"), crawled={5600})]
    assert got == [5600]


def test_cancelled_and_untracked_are_skipped():
    cal = [{**r, "status": "cancelled"} if r["tournament_id"] == 5766 else r for r in CAL]
    live_only = [{"tournament_id": 9999, "code": "X", "name": "Somewhere Junior", "start_date": "2026-09-30",
                  "end_date": "2026-10-01", "source_url": None}]
    got = [t["tournament_id"] for t, _ in daily.select_targets(cal, live_only, D("2026-09-30"), crawled={5874})]
    assert got == [5874]


NEWS_PAGES = {"bwf": "bwf_news_2026-09-30.html", "bwfworldtour": "bwfworldtour_news_2026-09-30.html",
              "cna": "cna_aspt_2026-09-30.html", "nownews": "nownews_sport_2026-09-30.html",
              "ettoday": "news_ettoday_2026-10-01.html", "pts": "news_pts_2026-10-01.html", "tsna": "news_tsna_2026-10-01.html"}


class FakeResponse:
    def __init__(self, payload=None, text=""):
        self.payload = payload
        self.text = text

    def json(self):
        return self.payload


class FakeClient:
    """依網址回傳存好的真實回應。"""
    def __init__(self):
        self.calls = []
        self.rank = load("rankings_2026-w40_sample.json")

    def get(self, url, **params):
        self.calls.append((url, params))
        if url.endswith("vue-grouped-year-tournaments"):
            return FakeResponse(load("calendar_2026_sample.json"))
        if url.endswith("vue-current-live"):
            return FakeResponse(load("current_live_2026-09-30.json"))
        if url.endswith("day-matches"):
            day = params["date"]
            ok = params["tournamentCode"] == "1B95960C-1C1B-41E2-B7CF-120E8CA38CE3" and day == "2026-09-30"
            return FakeResponse(load("north_harbour_2026-09-30_sample.json") if ok else [])
        if url.endswith("vue-rankingweek"):
            return FakeResponse(self.rank["weeks"][:1])
        if url.endswith("vue-rankingtable"):
            if params["catId"] != 6:
                return FakeResponse({"results": {"data": [], "last_page": 0}})
            ms = self.rank["ms"]["results"]           # 節錄只有 3 筆，當成只有一頁
            return FakeResponse({"results": {**ms, "last_page": 1}})
        pages = {news.SOURCES[k]: f for k, f in NEWS_PAGES.items()}
        if url in pages:
            return FakeResponse(text=(FIXDIR / pages[url]).read_text(encoding="utf-8"))
        raise AssertionError(f"沒有預期的請求：{url}")


def test_run_twice_is_idempotent():
    con = crawler.connect(":memory:")
    first = daily.run(con, FakeClient(), D("2026-09-30"), verbose=False)
    assert first["errors"] == []
    assert first["tournaments"] == [5766, 5874]
    assert first["ranking_rows"] == 3

    counts = lambda: [con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                      for t in ("tournament", "match", "game", "ranking_snapshot")]
    before = counts()
    second_client = FakeClient()
    second = daily.run(con, second_client, D("2026-09-30"), verbose=False)
    assert counts() == before
    assert second["ranking_rows"] == 0                      # 這週排名已存，不重抓
    assert not any(u.endswith("vue-rankingtable") for u, _ in second_client.calls)
    runs = con.execute("SELECT run_date, matches_stored, errors FROM crawl_run ORDER BY run_id").fetchall()
    assert runs == [("2026-09-30", first["matches_stored"], None), ("2026-09-30", second["matches_stored"], None)]


def test_one_failing_step_does_not_stop_the_rest():
    class Broken(FakeClient):
        def get(self, url, **params):
            if url.endswith("vue-current-live"):
                raise ConnectionError("timeout")
            return super().get(url, **params)

    con = crawler.connect(":memory:")
    res = daily.run(con, Broken(), D("2026-09-30"), verbose=False)
    assert res["tournaments"] == [5766, 5874]
    assert len(res["errors"]) == 1 and res["errors"][0].startswith("live:")
    assert con.execute("SELECT errors FROM crawl_run").fetchone()[0].startswith("live:")


def test_publish_marks_sent_and_alerts_on_errors(monkeypatch):
    from brief import discord, llm
    monkeypatch.setattr(llm, "available", lambda: False)      # 測試不呼叫真的 API
    sent = []
    monkeypatch.setattr(discord, "webhook", lambda name, *a: name)
    monkeypatch.setattr(discord, "send", lambda url, text, **k: sent.append((url, text)) or 1)
    con = crawler.connect(":memory:")
    res = daily.run(con, FakeClient(), D("2026-09-30"), verbose=False)
    assert daily.publish(con, D("2026-09-30"), res) == []
    assert [u for u, _ in sent] == ["DISCORD_WEBHOOK_DAILY"]          # 沒錯誤就不告警
    text = sent[0][1]
    assert text.startswith("**羽球晨報 2026-09-30**")
    assert "2026 亞運" not in text                                      # notes 18:15：Super 100 以上改由 brief.watch 發
    assert con.execute("SELECT COUNT(*) FROM digest_item").fetchone()[0] == 0   # 07:45：今天日期的 IC 場次不列、不標記

    sent.clear()
    daily.publish(con, D("2026-09-30"), {**res, "errors": ["live: timeout"]})
    assert [u for u, _ in sent] == ["DISCORD_WEBHOOK_ALERTS"]          # 沒有新內容就不發晨報，只告警
    assert "live: timeout" in sent[0][1]


def test_publish_failure_does_not_mark_sent(monkeypatch):
    from brief import discord, llm
    monkeypatch.setattr(llm, "available", lambda: False)

    def no_webhook(name, *a):
        raise RuntimeError(f"沒有設定 {name}")
    monkeypatch.setattr(discord, "webhook", no_webhook)
    con = crawler.connect(":memory:")
    res = daily.run(con, FakeClient(), D("2026-09-30"), verbose=False)
    errors = daily.publish(con, D("2026-09-30"), res)
    assert [e.split(":")[0] for e in errors] == ["digest", "alert"]
    assert con.execute("SELECT COUNT(*) FROM digest_item").fetchone()[0] == 0   # 下次會再推
