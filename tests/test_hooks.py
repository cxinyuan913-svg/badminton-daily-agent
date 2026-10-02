"""伏筆機制（notes 10-02 13:45，R16）與關鍵對手脈絡（notes 13:35）。fixture 是真實資料庫的子集。"""
import json
from pathlib import Path

import pytest

from brief import crawler, foreign_names, hooks, results, story, storylines as sl

FIX = Path(__file__).parent / "fixtures" / "opponent_kim_seo.json"
KIM_SEO = 779


@pytest.fixture
def con(tmp_path):
    c = crawler.connect(str(tmp_path / "brief.db"))
    c.executescript(results.RESULT_TABLE + foreign_names.TABLE)
    c.execute("PRAGMA foreign_keys=OFF")                 # 子集：團體賽外層等參照不在 fixture 裡
    data = json.loads(FIX.read_text(encoding="utf-8"))
    for table, d in data.items():
        cols = ",".join(d["columns"])
        c.executemany(f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({','.join('?' * len(d['columns']))})", d["rows"])
    c.commit()
    return c


def _sf(con):
    """2026 亞運男雙四強：卡爾南多／馬丁 21-11 21-14 金元昊／徐承宰。"""
    row = con.execute("""SELECT m.match_id, m.match_date, m.tournament_id, CASE WHEN m.winner_side=1 THEN m.side1_id ELSE m.side2_id END
                         FROM match m WHERE ? IN (m.side1_id, m.side2_id) AND m.match_date='2026-09-28' AND m.round='SF'""",
                      (KIM_SEO,)).fetchone()
    return {"match_id": row[0], "date": row[1], "tournament_id": row[2], "winner": row[3]}


def test_opponent_context_carnando_sf(con):
    """回歸（notes 13:35 第 4 點）：要有 2026-09-01 中國大師賽 32 強的交手，且 11-21 14-21 是 2025 年以來最懸殊的敗場。"""
    sf = _sf(con)
    ctx = sl.opponent_context(con, sf["winner"], KIM_SEO, "MD", sf["match_id"], sf["date"], "2026-09-23")
    text = "\n".join(ctx)
    assert ctx[0].startswith("【關鍵對手：金元昊／徐承宰】賽前排名：世界第 1")
    assert "2026-09-01 2026 中國大師賽32 強 卡爾南多／馬丁勝（卡爾南多／馬丁角度 21-19 21-19）" in text
    assert "這場（金元昊／徐承宰角度 11-21 14-21）是金元昊／徐承宰 2025 年以來" in text and "最懸殊的一場" in text
    assert "輸給 Muhammad Shohibul FIKRI 5 次（Muhammad Shohibul FIKRI換了 3 個搭檔）" in text
    assert "團體" not in text.split("之後各站：")[1].split("\n")[0]         # TEAM 名次不列
    # 給伏筆用的版本：剋星那幾條不放主線
    quiet = "\n".join(sl.opponent_context(con, sf["winner"], KIM_SEO, "MD", sf["match_id"], sf["date"], "2026-09-23", nemesis=False))
    assert "FIKRI" not in quiet and "2026-09-01" in quiet


def test_nemesis_branch_keys_exclude_protagonist(con):
    sf = _sf(con)
    b = sl.nemesis_branch(con, sf["winner"], KIM_SEO, "MD", sf["date"])
    assert b["subject"] == [KIM_SEO] and "FIKRI" in b["keys"]
    assert "馬丁" not in b["keys"] and "卡爾南多" not in b["keys"]          # 主角的名字一定要能寫
    assert "Muhammad" not in b["keys"]                                     # 常見名不當關鍵字
    assert len(b["answer"]) == 6


BR = [{"title": "金元昊／徐承宰近 2 年的剋星", "hint": "有個剋星", "answer": ["輸給 FIKRI 5 次"], "keys": ["FIKRI", "5 次"],
       "subject": [KIM_SEO]}]


def test_check_teaser_must_not_leak_answer():
    body = "四強打掉世界第一。不過他們近兩年其實有一個剋星，FIKRI 最常贏他們。"
    assert "洩漏" in hooks.check({"branch": "B1", "teaser": "不過他們近兩年其實有一個剋星"}, body, BR)[0]
    ok = "四強打掉世界第一。不過他們近兩年其實有一個剋星，下次再說。"
    assert hooks.check({"branch": "B1", "teaser": "不過他們近兩年其實有一個剋星，下次再說。"}, ok, BR) == []


def test_check_one_hook_and_present():
    body = "正文。有個剋星，下次說。"
    assert hooks.check(None, body, BR) == ["沒有伏筆（R16：挑一條支線素材埋一句懸念，輸出 hook）"]
    assert hooks.check({"branch": "B1", "teaser": ["有個剋星", "還有一個"]}, body, BR) == ["伏筆只能一個"]
    assert hooks.check({"branch": "B2", "teaser": "有個剋星"}, body, BR) == ["伏筆用了不存在的支線 B2"]
    assert hooks.check({"branch": "B1", "teaser": "沒寫進去的句子"}, body, BR) == ["伏筆句不在正文裡"]
    assert hooks.check(None, body, []) == []                                # 沒有支線素材就不要求


def test_story_write_retries_until_hook_ok(con, monkeypatch):
    """寫手第一版洩漏答案 → 退回；第二版只寫懸念 → 通過。一篇只存一個伏筆。"""
    monkeypatch.setattr(story, "check", lambda out, facts, flags: [])
    seg = lambda t: {"segments": [{"time": "0–4s", "part": "開場", "sentences": [{"text": t, "fact_ids": [], "kind": "rhetoric"}]}],
                     "titles": ["t"], "hook": {"branch": "B1", "teaser": t}}
    replies = [json.dumps(seg("他們有個剋星叫 FIKRI。"), ensure_ascii=False), json.dumps(seg("他們有個剋星，下次說。"), ensure_ascii=False)]

    class LLM:
        def complete(self, system, user):
            return replies.pop(0)
    out, problems = story.write(LLM(), "u", ["F"], con, {"day": "2026-10-02"}, branches=BR)
    assert problems == [] and out["hook"]["teaser"] == "他們有個剋星，下次說。" and not replies
    hid = hooks.save_from_output(con, out, BR, "script", "2026-10-02_x")
    assert [h["hook_id"] for h in hooks.open_hooks(con) if h["source_ref"] == "2026-10-02_x"] == [hid]


def test_hook_lifecycle(con):
    hid = hooks.add(con, "fb", "2026-10-02", [KIM_SEO], "剋星", "有個剋星", ["FIKRI 5 次"])
    con.execute("UPDATE story_hook SET created_at='2026-09-30 00:00:00' WHERE hook_id=?", (hid,))
    assert hooks.expire(con, "2026-10-05") == []                            # 沒過期、之後沒新比賽
    assert hooks.weekly_lines(con, "2026-10-05")[0] == f"伏筆：open 1 個：#{hid} 剋星"
    hooks.fill(con, hid, "fb 2026-10-06")
    assert hooks.open_hooks(con) == []
    old = hooks.add(con, "fb", "2026-09-01", [99999], "舊的", "x", ["y"])
    con.execute("UPDATE story_hook SET created_at='2026-09-01 00:00:00' WHERE hook_id=?", (old,))
    lines = hooks.weekly_lines(con, "2026-10-05")
    assert lines[0] == "伏筆：open 0 個" and "超過 21 天沒填" in lines[1]


def test_hook_dropped_when_subject_plays_again(con):
    hid = hooks.add(con, "fb", "2026-09-20", [KIM_SEO], "剋星", "有個剋星", ["FIKRI 5 次"])
    con.execute("UPDATE story_hook SET created_at='2026-09-20 00:00:00' WHERE hook_id=?", (hid,))
    dropped = hooks.expire(con, "2026-10-01")
    assert dropped and "有新比賽" in dropped[0]["drop_reason"]               # 9/28 亞運四強在伏筆之後


def test_fbpost_trivia_fills_oldest_hook(con):
    from brief import fbpost
    a = hooks.add(con, "fb", "2026-10-01", [], "剋星", "有個剋星", ["FIKRI 5 次"])
    hooks.add(con, "fb", "2026-10-02", [], "新的", "y", ["z"])
    con.execute("UPDATE story_hook SET created_at='2026-10-01 00:00:00' WHERE hook_id=?", (a,))
    kind, facts, info = fbpost.trivia(con, "2026-10-04")                     # 週日：不是排名日
    assert kind == "hook" and info["hook_id"] == a
    assert "2026-10-01 的粉專貼文埋了伏筆「有個剋星」" in facts[0] and "FIKRI 5 次" in facts
