"""Grade 3（IC／IS）日報規則測試：每條例外至少一個正例、一個反例。"""
import datetime as dt

from brief import crawler, digest, grade3, results
from tests.test_digest import db

D = dt.date(2026, 10, 1)


def ic_db():
    con = db(level="IC")
    _, matches, _, _ = digest.build(con, D)
    return con, matches


def first_player(matches):
    side = matches[0]["loser"]
    return side["players"][0][0], side["pairing_id"], matches[0]["event"]


def test_ic_is_hidden_with_count():
    con, matches = ic_db()
    text = digest.build(con, D)[0]
    assert "MAXX North Harbour" not in text
    assert f"今天另有 IC／IS 共 {len(matches)} 場，已存入資料庫。" in text


def test_rule_rank_top30():
    con, matches = ic_db()
    pid, pairing, event = first_player(matches)
    con.execute("INSERT INTO ranking_snapshot (week_date, event, pairing_id, rank) VALUES ('2026-09-22', ?, ?, 31)",
                (event, pairing))
    assert "值得一提" not in digest.build(con, D)[0]                     # 反例：最好只到 31 名
    con.execute("INSERT INTO ranking_snapshot (week_date, event, pairing_id, rank) VALUES ('2026-08-11', ?, ?, 12)",
                (event, pairing))
    text = digest.build(con, D)[0]
    assert "值得一提：前世界第 12 名（" in text
    assert "2026-08-11 官方排名" in text and "出現在 MAXX North Harbour International 2026（國際挑戰賽）" in text


def add_result(con, tid, name, level, pairing, event, rnd, date="2026-07-26"):
    con.executescript(results.RESULT_TABLE)
    con.execute("INSERT INTO tournament (tournament_id, name, level) VALUES (?, ?, ?)", (tid, name, level))
    con.execute("INSERT INTO tournament_result VALUES (?, ?, ?, ?, 0, 'V2024W17', ?)", (pairing, tid, event, rnd, date))


def test_rule_career_super750_quarterfinal():
    con, matches = ic_db()
    pid, pairing, event = first_player(matches)
    add_result(con, 9001, "VICTOR China Open 2026", "S1000", pairing, event, "R16")     # 反例：只到 16 強
    add_result(con, 9002, "Indonesia Masters 2026", "S500", pairing, event, "QF")       # 反例：S500 不夠高
    assert "值得一提" not in digest.build(con, D)[0]
    add_result(con, 9003, "Japan Open 2026", "S750", pairing, event, "QF")
    assert "值得一提：曾在 2026 日本公開賽（超級 750）打進八強" in digest.build(con, D)[0]


def test_rule_career_ignores_results_after_match_date():
    con, matches = ic_db()
    pid, pairing, event = first_player(matches)
    add_result(con, 9004, "Japan Open 2026", "S750", pairing, event, "W", date="2026-11-01")
    assert grade3.career_reason(con, pid, "2026-09-30") is None


def side(pid, home=True, name=None):
    return {"pairing_id": pid, "home": home, "name": name or f"選手{pid}", "players": []}


def m(rnd, w, l, event="MS"):
    return {"round": rnd, "winner": w, "loser": l, "event": event}


def test_podium_status_tpe_only():
    a, b, c, x = side(1), side(2), side(3), side(9, home=False)
    got = grade3.podium_status([m("Final", a, b), m("SF", a, c), m("SF", b, x)])
    assert got == {("MS", 1): "冠軍", ("MS", 2): "亞軍", ("MS", 3): "季軍"}         # 決賽蓋過四強的狀態；外國選手不列
    assert grade3.podium_status([m("SF", x, a)]) == {("MS", 1): "季軍"}
    assert grade3.podium_status([m("SF", a, x)]) == {("MS", 1): "確定至少亞軍"}     # 決賽明天
    assert grade3.podium_status([m("QF", a, x)]) == {("MS", 1): "確定至少季軍"}
    assert grade3.podium_status([m("R16", a, x), m("QF", x, a)]) == {}             # 反例：八強就輸
    assert grade3.podium_status([m("Final", x, side(8, home=False))]) == {}        # 反例：沒有台灣選手


def test_podium_line_format():
    a = side(1, name="林俊易（LIN Chun-Yi）")
    b = side(2, name="HUNG En-Tzu / HSIEH Pei Shan")
    line = grade3.podium_line("Portugal International Series 2026", "IS",
                              [m("Final", side(7, home=False), a), m("SF", side(6, home=False), b, event="WD")])
    assert line == "- IC／IS｜Portugal International Series 2026（國際系列賽）：林俊易（LIN Chun-Yi）男單亞軍、HUNG En-Tzu / HSIEH Pei Shan 女雙季軍"


def test_notable_lists_only_last_match():
    """22:45 決議：值得一提 = 一行理由＋該選手當天最後一場。"""
    con, matches = ic_db()
    pid, pairing, event = first_player(matches)
    con.execute("INSERT INTO ranking_snapshot (week_date, event, pairing_id, rank) VALUES ('2026-08-11', ?, ?, 12)",
                (event, pairing))
    text = digest.build(con, D)[0]
    i = text.index("值得一提")
    block = text[i:].splitlines()
    assert block[1].startswith("  - ") and not (len(block) > 2 and block[2].startswith("  - "))


def test_morning_only_yesterday_and_unsent_day_before():
    """07:45 修正：晨報的 IC／IS 只取台北昨天＋前天未推，不含今天、不含上週。"""
    import datetime as dt
    con = db(level="IC")
    con.execute("INSERT INTO tournament (tournament_id, name, level, start_date, end_date) "
                "VALUES (9100, 'Other Super 100 2026', 'S100', '2026-09-28', '2026-10-04')")   # 讓這週不是空檔週
    ids = [r[0] for r in con.execute("SELECT match_id FROM match WHERE tournament_id=5766 ORDER BY match_id")]
    con.execute("INSERT INTO ranking_snapshot (week_date, event, pairing_id, rank) "
                "SELECT '2026-08-11', event, side2_id, 12 FROM match WHERE tournament_id=5766")   # 讓每場都有「值得一提」
    def shown(day):
        text = digest.morning(con, dt.date.fromisoformat(day))[0] or ""
        return text.count("值得一提")
    assert shown("2026-10-01") > 0                       # 9/30 = 昨天 → 列
    assert shown("2026-10-02") > 0                       # 9/30 = 前天、還沒推 → 列
    assert shown("2026-10-03") == 0                      # 9/30 = 三天前 → 不列
    assert shown("2026-09-30") == 0                      # 9/30 = 今天 → 不列


def test_morning_podium_only_on_final_day():
    from brief import grade3
    a = {"pairing_id": 1, "home": True, "name": "林俊易（LIN Chun-Yi）", "players": [(1, "林俊易（LIN Chun-Yi）", "TPE")]}
    x = {"pairing_id": 2, "home": False, "name": "X", "players": [(2, "X", "JPN")]}
    base = {"tournament_id": 1, "tournament": "Portugal International Series 2026", "level": "IS", "event": "MS",
            "date": "2026-09-30", "team_tie_id": None, "upset": None, "match_id": 1, "score": "21-10 21-10", "status": "Normal",
            "winner_rank": None, "loser_rank": None}
    import sqlite3
    con = crawler.connect(":memory:")
    digest.DIGEST_TABLE and con.executescript(digest.DIGEST_TABLE)
    sf = [{**base, "round": "QF", "winner": a, "loser": x}]
    assert not any("IC／IS｜" in l for l in digest.grade3_section(con, sf, podium_on_final_only=True)[0])
    assert any("確定至少季軍" in l for l in digest.grade3_section(con, sf)[0])
    final = [{**base, "round": "Final", "winner": a, "loser": x}]
    assert any("男單冠軍" in l for l in digest.grade3_section(con, final, podium_on_final_only=True)[0])
