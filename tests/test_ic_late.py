"""notes 21:05：IC／IS 四強日、決賽日不論空檔週都由 watch 發，也能當故事素材；空檔週判斷排除已取消的賽事。"""
from pathlib import Path

import pytest

from brief import crawler, grade3


def _db():
    con = crawler.connect(":memory:")
    con.executemany("INSERT INTO tournament (tournament_id, name, level, start_date, end_date, status) VALUES (?,?,?,?,?,?)",
                    [(1, "Abu Dhabi Masters 2026 (Cancelled)", "S100", "2026-09-29", "2026-10-04", "cancelled"),
                     (2, "Azerbaijan Caspian Cup 2026 (Cancelled)", "S300", "2026-12-14", "2026-12-20", "normal"),
                     (3, "YONEX Dutch Open 2026", "IC", "2026-09-30", "2026-10-04", "normal"),
                     (4, "Some Super 300 2026", "S300", "2026-11-02", "2026-11-08", "normal")])
    con.commit()
    return con


def test_quiet_week_ignores_cancelled_by_status_or_name():
    con = _db()
    assert grade3.quiet_week(con, "2026-10-01")             # Abu Dhabi 狀態 cancelled
    assert grade3.quiet_week(con, "2026-12-16")             # 名稱帶 (Cancelled)、狀態還是 normal
    assert not grade3.quiet_week(con, "2026-11-04")


def test_late_day():
    con = _db()
    con.execute("INSERT INTO player (player_id, name_display) VALUES (1, 'A'), (2, 'B')")
    con.execute("INSERT INTO pairing (pairing_id, player_a_id) VALUES (1, 1), (2, 2)")
    con.executemany("INSERT INTO match (match_id, tournament_id, event, round, match_date, side1_id, side2_id, winner_side) "
                    "VALUES (?,?,?,?,?,?,?,?)", [(10, 3, "MS", "QF", "2026-10-02", 1, 2, 1), (11, 3, "MS", "SF", "2026-10-03", 1, 2, 1)])
    assert not grade3.late_day(con, 3, "2026-10-02") and grade3.late_day(con, 3, "2026-10-03")


DB = Path(__file__).resolve().parent.parent / "data" / "brief.db"


@pytest.mark.skipif(not DB.exists(), reason="需要本機的十年回補資料庫")
def test_ic_story_candidates_only_from_semis():
    from brief import story, zh
    con = crawler.connect(str(DB))
    zh.apply_player_names(con)
    row = con.execute("""SELECT t.tournament_id, t.name, t.level, t.start_date, t.end_date, m.match_date FROM tournament t
                         JOIN match m USING (tournament_id) WHERE t.level='IC' AND m.round='QF' AND t.end_date < '2026-09-01'
                         ORDER BY t.end_date DESC LIMIT 1""").fetchone()
    t = dict(zip(["tournament_id", "name", "level", "start_date", "end_date"], row[:5]))
    cands, daym, _ = story.day_candidates(con, t, row[5])
    assert all(m["round"] in grade3.LATE_DAY_ROUNDS for m in daym)    # 八強日沒有 IC 故事素材
