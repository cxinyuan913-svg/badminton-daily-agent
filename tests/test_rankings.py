"""排名快照測試：使用 2026 第 40 週的真實 API 回應（節錄）。"""
import json
from pathlib import Path

from brief import crawler, rankings

FIX = json.loads((Path(__file__).parent / "fixtures" / "rankings_2026-w40_sample.json").read_text(encoding="utf-8"))


def db():
    return crawler.connect(":memory:")


def test_store_singles_and_doubles():
    con = db()
    assert rankings.store_rows(con, "2026-09-29", "MS", FIX["ms"]["results"]["data"]) == 3
    assert rankings.store_rows(con, "2026-09-29", "MD", FIX["md"]["results"]["data"]) == 3
    top_md = con.execute("""SELECT p.player_a_id, p.player_b_id, r.points FROM ranking_snapshot r
                            JOIN pairing p USING (pairing_id) WHERE r.event='MD' AND r.rank=1""").fetchone()
    assert top_md == (61444, 66513, 114099.0)


def test_rerun_same_week_is_idempotent():
    con = db()
    rows = FIX["ms"]["results"]["data"]
    rankings.store_rows(con, "2026-09-29", "MS", rows)
    rankings.store_rows(con, "2026-09-29", "MS", rows)
    assert con.execute("SELECT COUNT(*) FROM ranking_snapshot").fetchone()[0] == 3


def test_rank_on_uses_latest_week_before_match():
    con = db()
    rows = FIX["ms"]["results"]["data"]
    rankings.store_rows(con, "2026-09-22", "MS", [{**rows[0], "rank": 2}])
    rankings.store_rows(con, "2026-09-29", "MS", [rows[0]])
    pid = crawler.pairing_id(con, [64032])
    assert rankings.rank_on(con, pid, "MS", "2026-09-25") == 2   # 比賽在第 39 週與第 40 週之間
    assert rankings.rank_on(con, pid, "MS", "2026-09-30") == 1
    assert rankings.rank_on(con, pid, "MS", "2026-09-01") is None


def test_weeks_missing():
    con = db()
    rankings.store_rows(con, "2026-09-29", "MS", FIX["ms"]["results"]["data"][:1])
    missing = rankings.weeks_missing(con, FIX["weeks"])
    assert [w["week"] for w in missing] == [39]


def test_ranking_player_then_match_keeps_first_seen():
    """排名先建了只有 slug 的選手，之後比賽資料要補上名字與 first_seen。"""
    con = db()
    rankings.store_rows(con, "2026-09-29", "MS", FIX["ms"]["results"]["data"][:1])
    crawler.upsert_player(con, {"id": "64032", "firstName": "Kunlavut", "lastName": "VITIDSARN",
                                "nameDisplay": "Kunlavut VITIDSARN", "nameShort": "K VITIDSARN",
                                "countryCode": "THA", "slug": "kunlavut-vitidsarn"}, "2026-07-22")
    row = con.execute("SELECT name_display, first_seen FROM player WHERE player_id=64032").fetchone()
    assert row == ("Kunlavut VITIDSARN", "2026-07-22")


def test_rank_lookup_dropped_out_is_not_old_rank():
    """組合在最新一週掉出排名時，不能回傳好幾週前的舊名次（2026-09-30 修正）。"""
    con = db()
    rows = FIX["ms"]["results"]["data"]
    rankings.store_rows(con, "2026-09-22", "MS", [rows[0]])
    rankings.store_rows(con, "2026-09-29", "MS", [rows[1]])            # rows[0] 這週不在表上
    pid = crawler.pairing_id(con, [int(rows[0]["player1_id"])])
    assert rankings.rank_lookup(con, pid, "MS", "2026-09-25") == (rows[0]["rank"], "official")
    assert rankings.rank_lookup(con, pid, "MS", "2026-09-30")[0] is None          # 不能回傳上週的舊名次


def test_rank_lookup_falls_back_to_estimate_before_official_weeks():
    from brief import ranking_estimate
    con = db()
    rankings.store_rows(con, "2026-09-29", "MS", FIX["ms"]["results"]["data"][:1])
    con.executescript(ranking_estimate.ESTIMATE_TABLE)
    pid = crawler.pairing_id(con, [64032])
    con.execute("INSERT INTO ranking_estimate VALUES ('2019-09-03', 'MS', ?, 7, 50000, 10, 'computed')", (pid,))
    assert rankings.rank_lookup(con, pid, "MS", "2019-09-10") == (7, "estimate")
    assert rankings.rank_lookup(con, pid, "MS", "2019-08-01") == (None, None)       # 更早沒有估算
    assert rankings.rank_on(con, pid, "MS", "2019-09-10") == 7


HIST = json.loads((Path(__file__).parent / "fixtures" / "rankings_2019-08-06_ms_sample.json").read_text(encoding="utf-8"))


class FakeHistClient:
    def __init__(self, weeks_by_player=None):
        self.calls = []
        self.weeks = weeks_by_player or {}

    def get(self, url, **params):
        self.calls.append((url.rsplit("/", 1)[-1], params))

        class R:
            def __init__(self, p):
                self.p = p

            def json(self):
                return self.p
        if url.endswith("publication/weeks"):
            return R(self.weeks.get(params["playerId"], []))
        if params.get("catId") == 6:
            return R(HIST)
        return R({"results": {"data": [], "last_page": 0}})


def test_history_week_real_2019_08_06_and_one_request_per_event():
    """2019-08-06（publicationId 1497）男單真實回應：第 1 桃田 103,118、第 2 周天成 86,698。"""
    con = db()
    client = FakeHistClient()
    n = rankings.crawl_week(client, con, {"id": 1497, "date": "2019-08-06", "display": "Week 32 (2019-08-06)"},
                            max_rank=5, verbose=False)
    assert n == 5
    assert len([c for c in client.calls if c[1].get("catId") == 6]) == 1          # 到 max_rank 就不再請求下一頁
    top2 = con.execute("""SELECT p.player_a_id, r.points FROM ranking_snapshot r JOIN pairing p USING (pairing_id)
                          WHERE r.week_date='2019-08-06' AND r.event='MS' ORDER BY r.rank LIMIT 2""").fetchall()
    assert top2 == [(89785, 103118.0), (34810, 86698.0)]


def test_history_weeks_union_and_gaps():
    w = lambda d, i: {"id": i, "date": d, "display": d}
    seeds = {34810: [w("2017-01-03", 1), w("2017-01-10", 2), w("2017-01-31", 5),
                     w("2020-03-17", 9), w("2021-01-26", 10)],
             99: [w("2017-01-17", 3), w("2016-12-27", 0)]}
    con = db()
    weeks, gaps = rankings.history_weeks(FakeHistClient(seeds), con, extra_seeds=[99])
    assert [x["date"] for x in weeks] == ["2021-01-26", "2020-03-17", "2017-01-31", "2017-01-17", "2017-01-10", "2017-01-03"]
    assert gaps == [("2017-01-17", "2017-01-31"), ("2017-01-31", "2020-03-17")]   # 2016 週次不收；凍結期內不算缺口


def test_rank_lookup_outside_top100_in_history_weeks():
    con = db()
    rows = HIST["results"]["data"]
    rankings.store_rows(con, "2019-08-06", "MS", [{**r, "rank": 100} if i == 4 else r for i, r in enumerate(rows)])
    con.execute("INSERT INTO player (player_id) VALUES (999999)")
    pid = crawler.pairing_id(con, [999999])
    assert rankings.rank_lookup(con, pid, "MS", "2019-08-10") == (None, "outside100")
    from brief import digest
    assert digest._rank(None, "outside100") == "百名外"


def test_week_with_missing_event_is_retried_and_500_skipped():
    """2026-10-01：歷史排名抓到一半遇到 BWF API 500，整支程式停掉；缺項的週要能續跑補回。"""
    con = db()

    class Flaky(FakeHistClient):
        def get(self, url, **params):
            if params.get("catId") == 7:
                raise RuntimeError("500 Server Error")
            return super().get(url, **params)
    week = {"id": 1497, "date": "2019-08-06", "display": "Week 32"}
    rankings.crawl_week(Flaky(), con, week, max_rank=5, verbose=False)        # WS 失敗，不中斷
    assert rankings.weeks_missing(con, [week]) == [week]                      # 缺項 → 還算沒抓完
    client = FakeHistClient()
    rankings.crawl_week(client, con, week, max_rank=5, verbose=False)
    assert not any(c[1].get("catId") == 6 for c in client.calls)            # 男單已存，不重抓


def test_stale_official_week_falls_back_to_estimate():
    """2019 以前 API 多數週回 500，只存到零星幾週：太舊的官方週不採用，改用估算；凍結期例外。"""
    from brief import ranking_estimate
    con = db()
    rankings.store_rows(con, "2018-04-19", "WS", FIX["ms"]["results"]["data"][:1])
    pid = crawler.pairing_id(con, [64032])
    con.executescript(ranking_estimate.ESTIMATE_TABLE)
    con.execute("INSERT INTO ranking_estimate VALUES ('2018-09-04', 'WS', ?, 9, 50000, 10, 'computed')", (pid,))
    assert rankings.rank_lookup(con, pid, "WS", "2018-04-25") == (1, "official")        # 一週內：官方
    assert rankings.rank_lookup(con, pid, "WS", "2018-09-10") == (9, "estimate")        # 半年前的官方週：不採用
    rankings.store_rows(con, "2020-03-17", "MS", FIX["ms"]["results"]["data"][:1])
    assert rankings.rank_lookup(con, pid, "MS", "2020-10-01")[1] == "official"            # 凍結期沿用
