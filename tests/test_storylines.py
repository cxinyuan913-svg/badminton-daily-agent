"""故事候選資料層（notes 15:40）：純函式用造的交手紀錄，其餘用本機十年資料庫（沒有就略過）。"""
from pathlib import Path

import pytest

from brief import storylines as sl


def ms(seq):
    return [{"a_won": c == "W", "round": "QF", "games": [], "duration": None, "tournament": "x", "date": "2026-01-01"} for c in seq]


def test_turning_point_and_streak():
    k, gap = sl.turning_point(ms("LLLWLLLWWWWWWWWW"))
    assert k == 7 and gap > 0.7                      # 前 7 場 1 勝 6 負 → 之後 9 連勝
    assert sl.turning_point(ms("WLWLWLWLWL")) is None   # 沒有翻轉
    assert sl.streak(ms("LLWWW")) == (True, 3)
    assert sl.streak(ms("WWL")) == (False, 1)


DB = Path(__file__).resolve().parent.parent / "data" / "brief.db"
needs_db = pytest.mark.skipif(not DB.exists(), reason="需要本機的十年回補資料庫")


def _t(con, tid):
    return dict(zip(["tournament_id", "name", "level", "start_date", "end_date"], con.execute(
        "SELECT tournament_id, name, level, start_date, end_date FROM tournament WHERE tournament_id=?", (tid,)).fetchone()))


@needs_db
def test_rivalry_an_yamaguchi_asian_games():
    from brief.crawler import connect
    con = connect(str(DB))
    t = _t(con, 5874)
    allm = sl.tournament_matches(con, 5874, t["end_date"])
    final = [m for m in allm if m["event"] == "WS" and m["round"] == "Final"][0]
    riv = [c for c in sl.candidates_for_match(con, final) if c["kind"] == "rivalry"][0]
    text = "\n".join(riv["facts"])
    assert "23 勝 15 負" in text and "連勝 9 場" in text and "2017 年以來" in text
    assert "9 勝 0 負" in text                         # 翻轉點之後全勝


@needs_db
def test_retired_final_denmark_2021():
    """2021 丹麥公開賽女單決賽，安洗瑩退賽、山口茜奪冠。"""
    from brief.crawler import connect
    con = connect(str(DB))
    t = _t(con, 3971)
    allm = sl.tournament_matches(con, 3971, t["end_date"])
    final = [m for m in allm if m["event"] == "WS" and m["round"] == "Final"][0]
    ret = [c for c in sl.candidates_for_match(con, final) if c["kind"] == "retired"]
    assert ret and "中途退賽" in ret[0]["facts"][1] and "AN Se Young" in ret[0]["facts"][1]


@needs_db
def test_candidates_only_use_database_facts():
    """每則候選的第一條都是那場比賽本身；排名只有官方（沒有「估算」字樣）。"""
    from brief.crawler import connect
    con = connect(str(DB))
    t = _t(con, 5601)
    allm = sl.tournament_matches(con, 5601, t["end_date"])
    cs = sl.story_candidates(con, t, allm, allm)
    assert {c["kind"] for c in cs} >= {"rivalry", "stuck_round", "record"}
    assert all("估算" not in f for c in cs for f in c["facts"])


@needs_db
def test_career_facts_carnando_marthin():
    """notes 21:15／R8：印尼男雙生涯最高世界第 9，是重返不是黑馬。"""
    from brief import story, zh
    from brief.crawler import connect
    con = connect(str(DB))
    zh.apply_player_names(con)
    t = _t(con, 5874)
    allm = sl.tournament_matches(con, 5874, t["end_date"])
    sf = [m for m in allm if m["event"] == "MD" and m["round"] == "SF" and m["loser_rank"] == 1][0]
    text = "\n".join(sl.career(con, sf["winner"]["pairing_id"], "MD", sf["winner"]["name"], sf["date"]))
    assert "生涯最高官方排名：世界第 9" in text and "比生涯最高低 37 名" in text and "冠軍 4 次" in text
    assert "2022 2022" not in text                                   # 年份不重複
    cands, daym, allm = story.day_candidates(con, t, "2026-09-29")
    facts = story.materials(con, t, "2026-09-29", story.pick(cands)[0], cands, daym, allm)
    # R11：素材固定三區；生涯放在【歷史】
    assert facts.index("【歷史】") < facts.index("【新聞】") < facts.index("【冷知識】")
    assert any("生涯最高官方排名" in f for f in facts[facts.index("【歷史】"):facts.index("【新聞】")])


@needs_db
def test_r9_eventual_champion_only_for_early_losses():
    """notes 23:10／R9：四強、決賽輸球的對手本來就是冠亞軍，不產生「對手最後拿到…」；八強以前才有。"""
    from brief import zh
    from brief.crawler import connect
    con = connect(str(DB))
    zh.apply_player_names(con)
    t = _t(con, 5874)
    allm = sl.tournament_matches(con, 5874, t["end_date"])
    lines = sl.taiwan_facts(con, t, allm, whole=False)
    sf = [l for l in lines if l.startswith("混雙 四強") and "詹又蓁" in l][0]
    qf = [l for l in lines if l.startswith("女雙 八強") and "林芝昀" in l][0]
    assert "對手最後拿到" not in sf
    assert "對手最後拿到冠軍（金牌）" in qf


@needs_db
def test_partner_history_carnando_marthin():
    """notes 10-02 07:00／R12：雙打排名起落先查搭檔史。claude.ai 查過的時間線要一字不差地在 facts 裡。"""
    from brief import zh
    from brief.crawler import connect
    con = connect(str(DB))
    zh.apply_player_names(con)
    text = "\n".join(sl.partner_facts(con, 1957, "MD", "CARNANDO／MARTHIN", "2026-09-29"))
    for needle in ("2018-09 起一起打", "2024-06-06 之後拆夥", "2026-05-12 重組", "Bagas MAULANA（2024-08～2026-03，73 場）",
                   "Muhammad Shohibul FIKRI（2024-08～2025-05，46 場）", "重組後第一站：2026 泰國公開賽，成績：冠軍",
                   "第 157 名", "第 43 名", "原因不明"):
        assert needle in text, needle
    assert "2020" not in text.split("拆夥")[0]               # 2020 疫情停賽的空檔不算拆夥（中間沒有別的搭檔）
