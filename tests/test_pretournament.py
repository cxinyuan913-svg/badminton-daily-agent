"""10-03 Raymond：大賽賽前看點（Super 750 以上、Grade 1；開賽前一天 18:00；粉專＋影片各一）。"""
import json
import sqlite3
from pathlib import Path

import pytest

from brief import pretournament

FIX = Path(__file__).parent / "fixtures"
DB = Path(__file__).resolve().parent.parent / "data" / "brief.db"
needs_db = pytest.mark.skipif(not DB.exists(), reason="需要本機的十年回補資料庫")


def test_upcoming_only_big_events_starting_tomorrow():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE tournament (tournament_id INTEGER, name TEXT, level TEXT, start_date TEXT, end_date TEXT, code TEXT, status TEXT)")
    con.executemany("INSERT INTO tournament VALUES (?,?,?,?,?,?,NULL)", [
        (1, "VICTOR Denmark Open 2026", "S750", "2026-10-13", "2026-10-18", "A"),
        (2, "Some International Challenge", "IC", "2026-10-13", "2026-10-18", "B"),
        (3, "HSBC BWF World Tour Finals 2026", "WTF", "2026-12-09", "2026-12-13", "C"),
        (4, "Cancelled Open 2026 (Cancelled)", "S1000", "2026-10-13", "2026-10-18", "D")])
    assert [t["tournament_id"] for t in pretournament.upcoming(con, "2026-10-12")] == [1]
    assert [t["tournament_id"] for t in pretournament.upcoming(con, "2026-12-08")] == [3]
    assert pretournament.upcoming(con, "2026-10-13") == []


@needs_db
def test_facts_from_real_schedule():
    """真實賽程回應（North Harbour 首日）：種子、台灣選手對戰（含對手近況）都要在事實清單裡。"""
    from brief import zh
    con = sqlite3.connect(DB)
    zh.apply_player_names(con)
    t = {"tournament_id": 5766, "name": "MAXX North Harbour International 2026", "level": "IC",
         "start_date": "2026-10-01", "end_date": "2026-10-04"}
    schedule = json.loads((FIX / "north_harbour_2026-10-01_schedule.json").read_text(encoding="utf-8"))
    facts = pretournament.facts_for(con, t, schedule)
    text = "\n".join(facts)
    assert "男單 種子：1 號 王柏崴" in text
    assert "【首日台灣選手的對戰與對手分析】" in facts and "對手 Ricky TANG 最近 5 站" in text


def test_run_pushes_post_and_script_once(monkeypatch, tmp_path):
    from brief import fbpost, script, story, watch
    con = sqlite3.connect(":memory:")
    t = {"tournament_id": 9, "name": "VICTOR Denmark Open 2026", "level": "S750", "start_date": "2026-10-13",
         "end_date": "2026-10-18", "code": "X"}
    monkeypatch.setattr(pretournament, "upcoming", lambda con, today, tid=None: [t])
    monkeypatch.setattr(watch, "_fetch", lambda client, code, day: [{"x": 1}])
    published = {"v": False}
    monkeypatch.setattr(watch, "schedule_published", lambda s: published["v"])
    monkeypatch.setattr(pretournament, "facts_for", lambda con, t, s: ["賽前看點：丹麥公開賽"])
    monkeypatch.setattr(fbpost, "OUT_DIR", tmp_path / "posts")
    monkeypatch.setattr(fbpost, "generate", lambda *a, **k: ({"body": "正文", "hashtags": ["#羽球"], "image": ""}, []))
    monkeypatch.setattr(story, "generate", lambda *a, **k: ({"titles": ["a"], "segments": [{"voice": "口播"}]}, []))
    fake = lambda: type("L", (), {"complete": lambda self, s, u: "{}"})()
    posts, scripts, alerts = [], [], []
    kw = dict(send_fb=posts.append, send_script=scripts.append, alert=alerts.append, make_llm=fake)
    assert pretournament.run(con, None, "2026-10-12", **kw)[0]["status"] == "no_schedule" and len(alerts) == 1
    published["v"] = True
    res = pretournament.run(con, None, "2026-10-12", **kw)
    assert res[0]["fb"] == "ok" and res[0]["script"] == "ok" and len(posts) == 1 and len(scripts) == 1
    assert pretournament.run(con, None, "2026-10-12", **kw) == []           # 同一站只產一次
    assert "賽前看點" in posts[0] and "賽前看點" in scripts[0]
