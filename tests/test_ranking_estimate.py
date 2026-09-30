"""排名重建測試：52 週視窗、取最好 10 站、同分規則、凍結期、前 100 名、冪等。"""
import datetime as dt

from brief import crawler, ranking_estimate as re_

D = dt.date


def test_rank_list_ties_and_top_n():
    scores = {1: (100, 3), 2: (90, 2), 3: (90, 2), 4: (90, 5), 5: (50, 1)}
    got = re_.rank_list(scores)
    assert [(r, p) for r, p, _, _ in got] == [(1, 1), (2, 4), (3, 2), (3, 3), (5, 5)]   # 同分站數多者在前，再相同就並列
    assert [p for _, p, _, _ in re_.rank_list(scores, top_n=3)] == [1, 4, 2, 3]          # 並列第 3 都保留


def test_window_and_best_of_ten():
    rows = [("2024-01-10", 7, 1000)]                                  # 52 週以外
    rows += [(f"2024-{m:02d}-15", 7, 100 * m) for m in range(3, 13)]   # 10 站
    rows += [("2025-01-20", 7, 50)]                                   # 第 11 站最低分，不計
    rows += [("2025-02-04", 7, 9999)]                                 # 基準日當天結束，不計
    s = re_.Estimator(rows).scores(D(2025, 2, 4))
    assert s[7] == (sum(100 * m for m in range(3, 13)), 11)


def test_tuesdays():
    ws = re_.tuesdays(D(2026, 9, 28), D(2026, 10, 14))
    assert ws == [D(2026, 9, 29), D(2026, 10, 6), D(2026, 10, 13)] and all(w.weekday() == 1 for w in ws)


def db_with_results():
    con = crawler.connect(":memory:")
    con.executescript(__import__("brief.results", fromlist=["x"]).RESULT_TABLE)
    con.executescript(re_.ESTIMATE_TABLE)
    for pid in range(1, 131):
        con.execute("INSERT INTO player (player_id) VALUES (?)", (pid,))
        con.execute("INSERT INTO pairing (pairing_id, player_a_id) VALUES (?, ?)", (pid, pid))
        con.execute("INSERT INTO tournament (tournament_id, name) VALUES (?, 'T')", (pid,))
        con.execute("INSERT INTO tournament_result VALUES (?, ?, 'MS', 'W', ?, 'V2018', '2020-01-15')",
                    (pid, pid, 10000 - pid))
    con.commit()
    return con


def test_compute_top100_frozen_and_idempotent():
    con = db_with_results()
    weeks = [D(2020, 3, 17), D(2020, 6, 2), D(2021, 3, 2)]
    n = re_.compute(con, weeks, events=("MS",))
    assert n == re_.compute(con, weeks, events=("MS",))                # 重跑同樣筆數、不重複
    by_week = dict(con.execute("SELECT week_date, COUNT(*) FROM ranking_estimate GROUP BY 1"))
    assert by_week["2020-03-17"] == 100                                # 130 人只存前 100
    methods = dict(con.execute("SELECT week_date, method FROM ranking_estimate WHERE rank=1"))
    assert methods == {"2020-03-17": "computed", "2020-06-02": "frozen"}
    frozen = con.execute("SELECT pairing_id, points FROM ranking_estimate WHERE week_date='2020-06-02' AND rank=1").fetchone()
    assert frozen == (1, 9999)
    assert "2021-03-02" not in by_week                                  # 解凍後，52 週外的成績已過期


def test_points_expire_when_next_edition_held():
    """V6.0 §2.2：成績留到同一站下一屆舉辦，或 52 週，以先到者為準。"""
    rows = [("2025-07-27", 1, 13500, False, "2026-07-12"), ("2026-07-12", 1, 7400, False, None)]
    est = re_.Estimator(rows)
    assert est.scores(D(2026, 7, 7))[1] == (13500, 1)
    assert est.scores(D(2026, 7, 14))[1] == (7400, 1)                 # 下一屆提前在 52 週內舉辦，舊的立刻失效


def test_one_continental_championship_per_continent():
    """§9.1.3：52 週內每一洲只計最新一次洲際錦標賽。"""
    g = ("CONT_IND", "asia")
    rows = [("2025-10-01", 1, 9200, False, None, g), ("2026-04-12", 1, 6420, False, None, g),
            ("2026-04-12", 1, 5040, False, None, ("CONT_IND", "europe"))]
    assert re_.Estimator(rows).scores(D(2026, 5, 5))[1] == (6420 + 5040, 2)


def test_series_key_and_s1000_grade_lookup():
    from brief import ranking_points as rp
    assert rp.series_key("KAPAL API Indonesia Open 2025") == rp.series_key("POLYTRON Indonesia Open 2026") == "indonesia open"
    grades = {("china open", 2026): "13500", ("malaysia open", 2026): "12700"}
    assert rp.s1000_level("VICTOR China Open 2026", D(2026, 7, 21), grades) == "S1000"
    assert rp.s1000_level("PETRONAS Malaysia Open 2027", D(2027, 1, 5), grades) == "S1000_12700"   # 沿用最近一年
    assert rp.s1000_level("Some New Open 2026", D(2026, 5, 1), grades) == "S1000"                  # 查不到採最高級
