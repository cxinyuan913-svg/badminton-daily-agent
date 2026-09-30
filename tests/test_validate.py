"""排名驗證指標測試（人造資料，數字可手算）。"""
from brief import crawler, ranking_estimate, validate


def db():
    con = crawler.connect(":memory:")
    con.executescript(ranking_estimate.ESTIMATE_TABLE)
    for pid in range(1, 6):
        con.execute("INSERT INTO player (player_id, name_display) VALUES (?, ?)", (pid, f"P{pid}"))
        con.execute("INSERT INTO pairing (pairing_id, player_a_id) VALUES (?, ?)", (pid, pid))
    official = [(1, 1, 1000), (2, 2, 900), (3, 3, 800), (4, 4, 700)]
    estimate = [(1, 1, 1000), (2, 3, 880), (3, 2, 810), (5, 4, 600)]      # 4 號估算沒進榜，5 號多出來
    for pid, rank, pts in official:
        con.execute("INSERT INTO ranking_snapshot (week_date, event, pairing_id, rank, points) VALUES ('2026-09-29','MS',?,?,?)",
                    (pid, rank, pts))
    for pid, rank, pts in estimate:
        con.execute("INSERT INTO ranking_estimate VALUES ('2026-09-29','MS',?,?,?,5,'computed')", (pid, rank, pts))
    return con


def test_metrics():
    stats, cases = validate.compare(db())
    s = stats["MS"]
    assert (s["weeks"], s["official"], s["overlap"], s["exact"], s["within2"]) == (1, 4, 3, 1, 3)
    assert s["pts_err"] / s["pts_n"] == (0 + 20 + 10) / 3
    assert [c["pairing_id"] for c in cases] == [4]                  # 只有沒進榜的算大差異


def test_report_table():
    text = validate.report(db())
    assert "| MS | 1 | 75.0% | 25.0% | 75.0% | 75.0% | 10 |" in text
    assert "| 2026-09-29 | MS | P4 | 4 | 700 | 前 100 外 | — |" in text
