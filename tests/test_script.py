"""口播腳本測試：事實檢查、風格選擇、推送分流；真實資料（data/brief.db）存在時驗證亞運決賽日的事實清單。"""
import json
from pathlib import Path

import pytest

from brief import script

FACTS = ["賽事：2026 亞運（綜合運動會），當地 2026-09-29",
         "男雙 決賽：Leo Rolly CARNANDO / Daniel MARTHIN（印尼，世界 #43）勝 WANG Chang / LIANG Wei Keng（中國，世界 #3），比分 19-21 21-13 21-18",
         "Leo Rolly CARNANDO / Daniel MARTHIN先輸第一局 19-21，後兩局逆轉",
         "男雙冠軍（金牌）：Leo Rolly CARNANDO / Daniel MARTHIN（印尼）"]
FLAGS = {"upset": False, "comeback": True, "first_title": False}


def seg(voice, n=1):
    return [{"time": f"{i}s", "voice": voice, "card": ""} for i in range(n)]


VOICE = ("世界排名第 43 的印尼 Leo Rolly CARNANDO / Daniel MARTHIN，決賽先輸一局 19 比 21，"
         "接著 21 比 13、21 比 18 連扳兩局逆轉，擊敗世界第 3 的 WANG Chang / LIANG Wei Keng 拿下金牌。") * 2


def test_check_passes_and_blocks_fabrication():
    ok = {"titles": ["世界第 43 拿金牌？", "a", "b"], "segments": seg(VOICE)}
    assert script.check(ok, FACTS, FLAGS) == []
    fake = {"titles": ["a", "b", "c"], "segments": seg(VOICE + "兩組交手 5 勝 0 負。")}
    assert any("查不到" in p for p in script.check(fake, FACTS, FLAGS))           # 捏造的交手紀錄
    wrong_score = {"titles": ["a", "b", "c"], "segments": seg(VOICE.replace("21 比 13", "21 比 12"))}
    assert any("查不到" in p for p in script.check(wrong_score, FACTS, FLAGS))
    upset = {"titles": ["a", "b", "c"], "segments": seg(VOICE + "這是本屆最大爆冷。")}
    assert any("爆冷" in p for p in script.check(upset, FACTS, FLAGS))
    no_comeback = {**FLAGS, "comeback": False}
    assert any("逆轉" in p for p in script.check(ok, FACTS, no_comeback))
    short = {"titles": ["a", "b", "c"], "segments": seg("太短")}
    assert any("口播" in p for p in script.check(short, FACTS, FLAGS))
    assert script.check({"titles": ["a"], "segments": []}, FACTS, FLAGS)


class FakeLLM:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def complete(self, system, user):
        self.calls.append(user)
        return self.replies.pop(0)


def test_generate_retries_once_then_gives_up():
    bad = json.dumps({"titles": ["a", "b", "c"], "segments": seg(VOICE + "交手 9 勝 0 負")}, ensure_ascii=False)
    good = json.dumps({"titles": ["a", "b", "c"], "segments": seg(VOICE)}, ensure_ascii=False)
    llm = FakeLLM([bad, good])
    out, problems = script.generate(llm, "story", FACTS, FLAGS)
    assert out and problems == [] and "上一版沒有通過檢查" in llm.calls[1]
    out, problems = script.generate(FakeLLM([bad, bad]), "story", FACTS, FLAGS)
    assert out is None and problems


def test_styles_by_day_type():
    """交接單 005：每個比賽日都走故事引擎；決賽日看 SCRIPT_FINAL，其餘看 SCRIPT_DAILY。"""
    t = {"end_date": "2026-09-29"}
    assert script.styles_for_day(t, "2026-09-29", "Final") == ["story_main"]
    assert script.styles_for_day(t, "2026-09-26", "R16") == ["story_main"]
    assert script.flag_for("story_main", t, "2026-09-29") == "SCRIPT_FINAL"
    assert script.flag_for("story_main", t, "2026-09-28") == "SCRIPT_DAILY"


def test_routing_scripts_daily_alerts(monkeypatch, tmp_path):
    """10:25：腳本走 DISCORD_WEBHOOK_SCRIPTS、日報走 DAILY、告警走 ALERTS；沒設定或開關沒開就只寫檔。"""
    from brief import discord
    env = {"DISCORD_WEBHOOK_SCRIPTS": "https://hook/scripts", "DISCORD_WEBHOOK_DAILY": "https://hook/daily",
           "DISCORD_WEBHOOK_ALERTS": "https://hook/alerts", "SCRIPT_FINAL": "1"}
    monkeypatch.setattr(script, "_env", lambda k: env.get(k))
    sent = []
    monkeypatch.setattr(discord, "send", lambda url, text, **k: sent.append(url) or 1)
    sender = script.script_sender()
    sender("x")
    assert sent == ["https://hook/scripts"]
    monkeypatch.setattr(discord, "webhook", lambda name, *a: env[name])
    assert discord.webhook("DISCORD_WEBHOOK_DAILY") == "https://hook/daily"
    assert discord.webhook("DISCORD_WEBHOOK_ALERTS") == "https://hook/alerts"
    env.pop("DISCORD_WEBHOOK_SCRIPTS")
    assert script.script_sender() is None                                     # 沒設定 → 只寫檔


def test_push_only_when_flag_on(monkeypatch):
    out = {"titles": ["a", "b", "c"], "segments": seg(VOICE)}
    monkeypatch.setattr(script, "facts_for", lambda *a, **k: {"facts": FACTS, "flags": FLAGS})
    monkeypatch.setattr(script, "generate", lambda *a, **k: (out, []))
    t = {"tournament_id": 1, "name": "Asian Games 2026", "level": "MULTI", "start_date": "2026-09-25", "end_date": "2026-09-29"}
    for flag, expect in (("1", 1), (None, 0)):
        sent = []
        monkeypatch.setattr(script, "_env", lambda k: flag if k == "SCRIPT_FINAL" else None)
        script.run_for_day(None, t, "2026-09-29", "Final", make_llm=lambda p: None, send=sent.append, styles=["story"])
        assert len(sent) == expect


DB = Path(__file__).resolve().parent.parent / "data" / "brief.db"


@pytest.mark.skipif(not DB.exists(), reason="需要本機的十年回補資料庫")
def test_real_asian_games_final_facts_contain_example_numbers():
    """交接單 003：2026-09-29 亞運決賽日的事實清單要包含範例用到的數字。"""
    from brief.crawler import connect
    con = connect(str(DB))
    t = dict(zip(["tournament_id", "name", "level", "start_date", "end_date"],
                 con.execute("SELECT tournament_id, name, level, start_date, end_date FROM tournament WHERE tournament_id=5874").fetchone()))
    quick = "\n".join(script.facts_for(con, t, "2026-09-29", "quick")["facts"])
    for needle in ["#43", "#3", "19-21 21-13 21-18", "26-28 21-18 21-18", "8 勝 1 負"]:
        assert needle in quick, needle
    taiwan = "\n".join(script.facts_for(con, t, "2026-09-29", "taiwan")["facts"])
    assert "四強（銅牌）" in taiwan                                           # 葉宏蔚／詹又蓁混雙銅牌
    from brief import storylines as sl
    ch = con.execute("SELECT pairing_id FROM pairing WHERE player_a_id=34810 AND player_b_id IS NULL").fetchone()[0]
    fa = con.execute("SELECT pairing_id FROM pairing WHERE player_a_id=58089 AND player_b_id IS NULL").fetchone()[0]
    assert sl.h2h_record(con, ch, fa, "2026-09-29")[:2] == (3, 2)             # 周天成 vs 法漢 3 勝 2 負


def test_medal_words_allowed_everywhere_but_no_third_place():
    """notes 23:15（取代 15:40 第 1 點）：所有賽事都可以寫金銀銅（冠軍＝金、亞軍＝銀、四強＝銅）；不用「季軍」「第三名」。"""
    from brief import storylines as sl
    assert sl.place_name("S300", "F") == "亞軍（銀牌）" and sl.place_name("S300", "SF") == "四強（銅牌）"
    assert sl.place_name("MULTI", "W") == "冠軍（金牌）" and sl.place_name("IC", "QF") == "八強"
    facts = ["混雙 決賽：A / B（日本，世界 #5）勝 楊博軒／胡绫芳（中華台北，世界 #12），比分 21-19 21-8",
             "台灣 混雙 楊博軒／胡绫芳 本站最後名次：亞軍（銀牌）"]
    ok = {"titles": ["a", "b", "c"], "segments": seg(("混雙楊博軒／胡绫芳拿下銀牌，決賽輸給日本組合，第一局只差兩分。") * 5)}
    assert not [p for p in script.check(ok, facts, FLAGS) if "季軍" in p or "獎牌" in p]
    bad = {"titles": ["a", "b", "c"], "segments": seg(("混雙楊博軒／胡绫芳拿下季軍，決賽輸給日本組合，第一局只差兩分。") * 5)}
    assert any("季軍" in p for p in script.check(bad, facts, FLAGS))


def test_upset_by_rank_reference_only():
    """2026-10-01 試寫：「世界第 71 爆冷擊敗世界第 5」只寫排名，對得上規則判定的場次就放行。"""
    from brief.llm import unlicensed_upsets
    src = "女雙 決賽：Sumire NAKADE / Miyu TAKAHASHI（日本，世界 #71）勝 THINAAH Muralitharan / Pearly TAN（馬來西亞，世界 #5），比分 21-16 21-18，規則判定爆冷"
    assert unlicensed_upsets("世界第 71 爆冷擊敗世界第 5。", src, {}) == []
    assert unlicensed_upsets("世界第 71 爆冷擊敗世界第 50。", src, {})
    assert unlicensed_upsets("世界第 7 爆冷擊敗世界第 5。", src, {})          # #7 不能被 #71 矇混


def test_switches_default_off(monkeypatch):
    """開關預設全關：不產生、不花錢；dry 只寫檔；決賽日看 SCRIPT_FINAL、其餘看 SCRIPT_DAILY。"""
    t = {"end_date": "2026-09-29"}
    env = {}
    monkeypatch.setattr(script, "_env", lambda k: env.get(k))
    assert script.wanted_styles(t, "2026-09-29", "Final") == []
    env["SCRIPT_DAILY"] = "dry"
    assert script.wanted_styles(t, "2026-09-29", "Final") == []
    assert script.wanted_styles(t, "2026-09-28", "SF") == ["story_main"]
    env["SCRIPT_FINAL"] = "1"
    assert script.wanted_styles(t, "2026-09-29", "Final") == ["story_main"]


def test_cost_since_only_counts_scripts():
    import sqlite3
    from brief.llm import CALL_TABLE
    con = sqlite3.connect(":memory:")
    con.executescript(CALL_TABLE)
    con.executemany("INSERT INTO llm_call (called_at, purpose, task, model, input_tokens, output_tokens, cost_usd) VALUES (?,?,?,?,?,?,?)",
                    [("2026-09-29 01:00:00", "heavy", "script_quick", "m", 1, 1, 0.3),
                     ("2026-09-29 02:00:00", "routine", "highlight", "m", 1, 1, 0.9),
                     ("2026-09-30 02:00:00", "heavy", "script_story", "m", 1, 1, 0.2)])
    assert script.cost_since(con, "2026-09-29", "2026-09-30") == pytest.approx(0.3)
    assert script.cost_since(con, "2026-09-29") == pytest.approx(0.5)


@pytest.mark.skipif(not DB.exists(), reason="需要本機的十年回補資料庫")
def test_scripts_use_official_ranks_only():
    """notes 10:40：2018 年混雙、女雙沒有官方週（排名表 API 回 500）只有估算 → 事實清單不寫排名。
    （男單 2018 有 97 週官方資料，照常寫）"""
    from brief.crawler import connect
    con = connect(str(DB))
    cols = ["tournament_id", "name", "level", "start_date", "end_date"]
    t = dict(zip(cols, con.execute("SELECT tournament_id, name, level, start_date, end_date FROM tournament WHERE tournament_id=3141").fetchone()))
    facts = script.facts_for(con, t, "2018-03-18", "quick")["facts"]
    text = "\n".join(f for f in facts if f.startswith(("混雙", "女雙")))
    assert text and "#" not in text and "百名外" not in text and "無排名" not in text
    assert any("決賽" in f for f in facts)


def test_rank_label_only_official():
    from brief import storylines as sl
    assert sl._rk(5, "official") == "世界 #5"
    assert sl._rk(5, "estimate") is None and sl._rk(None, "outside100_est") is None and sl._rk(None, None) is None
    assert sl._rk(None, "outside100") == "百名外"
    m = sl.official_only({"winner_rank": 3, "winner_rank_src": "estimate", "loser_rank": 9, "loser_rank_src": "official"})
    assert m["winner_rank"] is None and m["loser_rank"] == 9


def test_upset_by_event_final():
    """notes 13:35（B）：「項目＋決賽／冠軍」且該項目決賽是規則判定的爆冷 → 放行；否則照擋。"""
    from brief.llm import unlicensed_upsets
    src = ("女雙 決賽：Sumire NAKADE / Miyu TAKAHASHI（日本，世界 #71）勝 THINAAH Muralitharan / Pearly TAN（馬來西亞，世界 #5），比分 21-16 21-18，規則判定爆冷\n"
           "男單 決賽：Yudai OKIMOTO（日本，世界 #26）勝 YOO Tae Bin（韓國，世界 #73），比分 21-15 21-18\n"
           "男單 四強：YOO Tae Bin（韓國，世界 #73）勝 林俊易（中華台北，世界 #12），比分 21-19 21-19，規則判定爆冷")
    assert unlicensed_upsets("台北公開賽女雙爆冷封后。", src, {}) == []
    assert unlicensed_upsets("男單決賽爆冷。", src, {})                    # 男單決賽不是爆冷（爆冷在四強）
    assert unlicensed_upsets("台北公開賽爆冷封后。", src, {})              # 沒寫項目
    assert unlicensed_upsets("女雙八強爆冷。", src, {})                    # 有項目，但不是決賽／冠軍


def test_rehashed_score_is_rejected():
    """notes 15:40 第 3 條：9/28 單一故事先說 21 比 11、21 比 14，再「從韓國角度」說 11 比 21、14 比 21。"""
    assert script.rehashed_scores("第一局 21 比 11，第二局 21 比 14。從韓國組合的角度看，就是 11 比 21、14 比 21。") == ["21-11", "21-14"]
    assert script.rehashed_scores("第一局 19 比 21 先丟，後兩局 21 比 13、21 比 18。") == []
    out = {"titles": ["a", "b", "c"], "segments": seg(VOICE + "從中國組合的角度看，是 13 比 21。")}
    assert any("換角度重講" in p for p in script.check(out, FACTS, FLAGS))


def test_short_script_allowed():
    """素材不夠就短：約 30 秒（120 字以上）可以通過。"""
    short = {"titles": ["a", "b", "c"], "segments": seg(VOICE[: len(VOICE) // 2 + 20])}
    assert not any("口播" in p for p in script.check(short, FACTS, FLAGS))


def test_foreign_name_variants_and_medals_in_highlight():
    """notes 15:40 第 1、2 條，今日重點也適用。"""
    from brief.llm import third_place_word, unverified
    src = "男單 決賽：昆拉武特（泰國，世界 #1）勝 LOH Kean Yew（新加坡，世界 #13），比分 21-12 21-16"
    assert "坤拉武特（譯名不一致）" in unverified("坤拉武特 21-12 21-16 奪冠", src, {})
    assert not [m for m in unverified("昆拉武特 21-12 21-16 奪冠", src, {}) if "譯名" in m]
    assert not third_place_word("昆拉武特拿下金牌") and third_place_word("拿下季軍") and third_place_word("拿到第三名")


def test_daily_budget_skips_scripts_and_alerts_once(monkeypatch):
    """notes 15:20：台北時間一天累計超過 LLM_DAILY_BUDGET_USD → 腳本跳過、告警只發一次。"""
    import sqlite3
    from brief.llm import CALL_TABLE, spent_taipei_day
    con = sqlite3.connect(":memory:")
    con.executescript(CALL_TABLE)
    # 台北 10-01 = UTC 09-30 16:00 ～ 10-01 16:00
    con.executemany("INSERT INTO llm_call (called_at, purpose, task, model, input_tokens, output_tokens, cost_usd) VALUES (?,?,?,?,?,?,?)",
                    [("2026-09-30 15:59:59", "heavy", "script_quick", "m", 1, 1, 5.0),     # 台北 09-30
                     ("2026-09-30 16:00:00", "routine", "highlight", "m", 1, 1, 0.6),
                     ("2026-10-01 15:00:00", "heavy", "script_story", "m", 1, 1, 0.5)])
    assert spent_taipei_day(con, "2026-10-01") == pytest.approx(1.1)
    monkeypatch.setattr(script, "_env", lambda k: "1.0" if k == "LLM_DAILY_BUDGET_USD" else None)
    assert script.over_budget(con, "2026-10-01") and not script.over_budget(con, "2026-10-02")
    alerts = []
    script.budget_alert(con, alerts.append, "2026-10-01")
    script.budget_alert(con, alerts.append, "2026-10-01")
    assert len(alerts) == 1 and "超過 US$1.00" in alerts[0]
    monkeypatch.setattr(script, "taipei_today", lambda: "2026-10-01")
    t = {"tournament_id": 1, "name": "x", "level": "S300", "start_date": "2026-10-01", "end_date": "2026-10-01"}
    res = script.run_for_day(con, t, "2026-10-01", "Final", make_llm=lambda p: None, send=lambda x: None, styles=["story"])
    assert res["styles"]["story"]["status"] == "skipped_budget"


def test_monthly_alert_once_per_threshold():
    """notes 16:55：當月累計 > US$25、> US$40 各推一次告警（每月一次），附各 stage 費用。"""
    import sqlite3
    from brief.llm import CALL_TABLE
    con = sqlite3.connect(":memory:")
    con.executescript(CALL_TABLE)
    con.executemany("INSERT INTO llm_call (called_at, purpose, task, model, input_tokens, output_tokens, cost_usd) VALUES (?,?,?,?,?,?,?)",
                    [("2026-10-01 00:00:00", "heavy", "fbpost", "m", 1, 1, 20.0),
                     ("2026-10-02 00:00:00", "heavy", "verify", "m", 1, 1, 6.0),
                     ("2026-09-30 15:00:00", "heavy", "fbpost", "m", 1, 1, 99.0)])     # 台北 9/30 23:00 → 上個月
    alerts = []
    assert script.monthly_alert(con, alerts.append, "2026-10-02") == [25.0]
    assert script.monthly_alert(con, alerts.append, "2026-10-03") == []                  # 同一個月不重發
    assert "fbpost US$20.00" in alerts[0] and "verify US$6.00" in alerts[0]
    con.execute("INSERT INTO llm_call (called_at, purpose, task, model, input_tokens, output_tokens, cost_usd) VALUES ('2026-10-05 00:00:00','heavy','fbpost','m',1,1,15.0)")
    assert script.monthly_alert(con, alerts.append, "2026-10-05") == [40.0] and len(alerts) == 2
