"""明日看點測試：時間換算（歐、美、亞）、oopText、五條選場規則各正反例、排序與上限、賽程未公布。"""
import copy
import json
from pathlib import Path

from brief import crawler, preview, watch

FIXDIR = Path(__file__).parent / "fixtures"
SCHED = json.loads((FIXDIR / "north_harbour_2026-10-01_schedule.json").read_text(encoding="utf-8"))
ON = "2026-10-01"


def test_time_labels_from_real_schedule():
    first = SCHED[0]                                                  # Starting at 10:00 AM（紐西蘭 UTC+13）
    assert first["oopText"].startswith("Starting at")
    assert preview.time_label(first) == "05:00（台灣）"
    follow = next(m for m in SCHED if m["oopText"] == "Followed by")
    tw = preview.taiwan_time(follow).strftime("%H:%M")
    assert preview.time_label(follow) == f"約 {tw} 後，接第 {int(follow['oopRound']) - 1} 場"
    nb = next(m for m in SCHED if m["oopText"].startswith("Not before"))
    assert preview.time_label(nb).startswith("不早於 ") and preview.time_label(nb).endswith("（台灣）")
    tba = next(m for m in SCHED if m["oopText"].startswith("Court and time TBA"))
    assert preview.time_label(tba) == "時間未定"


def with_time(local, utc):
    m = copy.deepcopy(SCHED[0])
    m.update({"matchTime": local, "matchTimeUtc": utc, "oopText": "Starting at"})
    return m


def test_timezones_europe_america_asia():
    europe = with_time("2026-10-15 14:00:00", "2026-10-15 12:00:00")   # 丹麥夏令 UTC+2
    america = with_time("2026-06-10 19:30:00", "2026-06-10 23:30:00")  # 美東 UTC-4
    asia = with_time("2026-09-26 13:00:00", "2026-09-26 04:00:00")     # 日本 UTC+9
    assert [preview.time_label(m) for m in (europe, america, asia)] == ["20:00（台灣）", "07:30（台灣）", "12:00（台灣）"]
    assert preview.taiwan_time(america).date().isoformat() == "2026-06-11"       # 跨日
    assert preview.local_offset(america).total_seconds() == -4 * 3600


def test_schedule_unpublished():
    assert not watch.schedule_published([])
    tba = [dict(m, oopText="Court and time TBA") for m in SCHED]
    assert not watch.schedule_published(tba)
    assert watch.schedule_published(SCHED)


# ---------------------------------------------------------------- 選場規則
def match(p1, p2, c1="JPN", c2="KOR", event="Men's Singles", t_utc="2026-10-01 02:00:00"):
    team = lambda ids, c: {"players": [{"id": str(i), "countryCode": c} for i in ids]}
    return {"id": 1, "isTeamMatch": False, "matchStatus": "N", "winner": 0, "matchTypeValue": event,
            "eventName": event, "roundName": "QF", "team1": team(p1, c1), "team2": team(p2, c2),
            "matchTime": t_utc, "matchTimeUtc": t_utc, "oopText": "Starting at", "courtName": "Court 1"}


def db():
    con = crawler.connect(":memory:")
    for pid in range(1, 30):
        con.execute("INSERT INTO player (player_id, name_display) VALUES (?, ?)", (pid, f"P{pid}"))
        con.execute("INSERT INTO pairing (pairing_id, player_a_id) VALUES (?, ?)", (pid, pid))
    con.execute("INSERT INTO tournament (tournament_id, name) VALUES (1, 'VICTOR China Open 2026')")
    return con


def rank(con, pid, r, week="2026-09-29"):
    con.execute("INSERT INTO ranking_snapshot (week_date, event, pairing_id, rank) VALUES (?, 'MS', ?, ?)", (week, pid, r))


def meet(con, winner, loser, date, rnd="R32", mid=[100]):
    mid[0] += 1
    con.execute("INSERT INTO match (match_id, tournament_id, event, round, match_date, side1_id, side2_id, winner_side) "
                "VALUES (?, 1, 'MS', ?, ?, ?, ?, 1)", (mid[0], rnd, date, winner, loser))


def why(con, m, tracked=frozenset()):
    return preview.reasons(con, m, ON, set(tracked))[0]


def test_rule_tpe():
    con = db()
    assert why(con, match([1], [2], c1="TPE")) == ["中華台北"]
    assert why(con, match([1], [2])) == []


def test_rule_both_top10():
    con = db()
    rank(con, 1, 3); rank(con, 2, 8); rank(con, 3, 11)
    assert "前 10 對決" in why(con, match([1], [2]))
    assert why(con, match([1], [3])) == []


def test_rule_rematch_of_recent_final():
    con = db()
    meet(con, 1, 2, "2026-07-26", "Final")
    meet(con, 3, 4, "2025-09-01", "Final")                   # 超過 12 個月
    meet(con, 5, 6, "2026-07-26", "QF")                      # 不是決賽或四強
    assert "上次 2026 中國公開賽決賽的重演" in why(con, match([1], [2]))
    assert not any("重演" in w for w in why(con, match([3], [4])))
    assert not any("重演" in w for w in why(con, match([5], [6])))


def test_rule_lopsided_but_recent_upset():
    con = db()
    for i in range(7):
        meet(con, 7, 8, f"2024-0{i + 1}-10")
    meet(con, 8, 7, "2026-05-01")                            # 1 勝 7 負的一方最近贏了
    for i in range(7):
        meet(con, 9, 10, f"2024-0{i + 1}-10")
    meet(con, 9, 10, "2026-05-01")                           # 強勢方最近也贏
    assert "交手懸殊，但最近一次弱勢方贏" in why(con, match([7], [8]))
    assert "交手懸殊，但最近一次弱勢方贏" not in why(con, match([9], [10]))


def test_rule_revenge_for_tracked_tpe():
    con = db()
    meet(con, 12, 11, "2026-03-01")                          # 今年輸過
    meet(con, 14, 13, "2025-03-01")                          # 去年輸的不算
    assert "復仇戰：對手今年贏過" in why(con, match([11], [12], c1="TPE"), tracked={11})
    assert "復仇戰：對手今年贏過" not in why(con, match([13], [14], c1="TPE"), tracked={13})
    assert "復仇戰：對手今年贏過" not in why(con, match([11], [12]), tracked=set())     # 非追蹤選手


def test_select_limit_priority_and_line_from_tpe_side():
    con = db()
    for pid in range(15, 29):
        rank(con, pid, pid - 14)                             # 15→#1 … 28→#14
    ms = [match([15 + 2 * i], [16 + 2 * i], t_utc=f"2026-10-01 0{i}:00:00") for i in range(5)]  # 前 10 對決 5 場
    ms += [match([20 + i], [1 + i], c2="TPE", t_utc="2026-10-01 09:00:00") for i in range(5)]    # 台灣 5 場
    got = preview.select(con, ms, ON, set())
    assert len(got) == 8
    assert sum("（無排名）" in g for g in got) == 5                                             # 台灣 5 場全留（台灣選手 P1–P5 無排名）
    assert got == sorted(got, key=lambda g: g.split("（台灣）")[0])                            # 依時間排序
    meet(con, 2, 25, "2026-06-01", "SF")
    line = preview.select(con, [match([25], [2], c2="TPE")], ON, set())[0]
    assert "：P2（無排名）vs P25（#11）" in line and "交手 1 勝 0 負，上次 2026 中國公開賽四強贏" in line
