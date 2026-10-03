"""10-03 Raymond：粉專週一台灣週報、週二排名變動報告（字數不限、不套 R7／R10）；寫手直接用 medium。"""
import sqlite3
from pathlib import Path

import pytest

from brief import fbpost, script

DB = Path(__file__).resolve().parent.parent / "data" / "brief.db"
needs_db = pytest.mark.skipif(not DB.exists(), reason="需要本機的十年回補資料庫")


def test_monday_weekly_and_tuesday_ranking(monkeypatch):
    con = sqlite3.connect(":memory:")
    monkeypatch.setattr(fbpost, "taiwan_week_facts", lambda con, today: ["上週…", "【某站】", "台灣 男單 A：本站名次 冠軍（金牌）"])
    monkeypatch.setattr(script, "weekly_facts", lambda con, week=None, full=False: {"week": "2026-10-06", "facts": ["排名"]} if full else None)
    assert fbpost.choose(con, "2026-10-05")[0] == "tw_weekly"          # 週一
    kind, facts, info = fbpost.choose(con, "2026-10-06")                # 週二、新排名 10-06
    assert kind == "ranking" and info["week"] == "2026-10-06"


def test_long_kinds_skip_number_and_side_limits():
    assert fbpost.body_range("tw_weekly")[1] >= 100000 and fbpost.body_range("ranking")[1] >= 100000
    body = "　".join(f"台灣 男單 第 {i} 名" for i in range(20)) + "\n\n" + fbpost.SIGNATURE
    long_probs = fbpost.check_form(body, 1, numbers=False)
    assert not any("數字" in p for p in long_probs)
    assert any("數字" in p for p in fbpost.check_form(body, 1))
    assert fbpost.EFFORTS == ("medium",)


@needs_db
def test_taiwan_week_facts_ordered_by_level():
    """真實資料：2026-09-28～10-04 那週，亞運（綜合運動會）排在國際挑戰賽前面；每站先列名次。"""
    from brief import zh
    con = sqlite3.connect(DB)
    zh.apply_player_names(con)
    facts = fbpost.taiwan_week_facts(con, "2026-10-05")
    heads = [f for f in facts if f.startswith("【")]
    assert heads and "亞運" in heads[0] and all("國際挑戰賽" in h for h in heads[1:])
    i = facts.index(heads[0])
    assert facts[i + 1].startswith("台灣 ") and "本站名次" in facts[i + 1]
