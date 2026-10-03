"""故事引擎（交接單 005）：選題、推測句不進事實檢查、推測要有待查清單。"""
from brief import story

FACTS = ["賽事：2026 亞運（綜合運動會），今天是當地 2026-09-29",
         "女單 決賽：AN Se Young（韓國，世界 #1）勝 Akane YAMAGUCHI（日本，世界 #3），比分 21-17 21-9",
         "AN Se Young對Akane YAMAGUCHI交手紀錄 23 勝 15 負（2017 年以來，共 38 場）",
         "AN Se Young目前對Akane YAMAGUCHI連勝 9 場"]
VOICE = ("曾經輸多贏少，現在連贏 9 場。AN Se Young 對 Akane YAMAGUCHI，2017 年以來交手 38 場，AN Se Young 23 勝 15 負。"
         "今天亞運決賽 21 比 17、21 比 9 再贏一次，世界第 1 對世界第 3，第二局只讓對手拿 9 分。") * 2


def out(voice, todo=None, **kw):
    return {"titles": ["a", "b", "c"], "value_kind": "歷史紀錄",
            "segments": [{"time": "0–50s", "part": "高潮還原", "voice": voice, "card": ""},
                         {"time": "50–55s", "part": "價值段", "voice": "", "card": ""},
                         {"time": "55–60s", "part": "留言問題", "voice": "", "card": ""}],
            "todo": todo or [], **kw}


def test_speculation_skipped_but_needs_todo():
    flags = story._flags(FACTS)
    assert story.check(out(VOICE), FACTS, flags) == []
    spec = VOICE + "山口茜比安洗瑩大將近 5 歲 ⚠️推測。"
    assert story.check(out(spec), FACTS, flags) == ["有推測句但沒有待查清單"]
    assert story.check(out(spec, [{"claim": "年齡差", "basis": "模型知識", "how": "BWF 選手頁"}]), FACTS, flags) == []
    # 沒標推測的資料庫外數字照樣擋
    assert any("查不到" in p for p in story.check(out(VOICE + "兩人年齡差 5 歲。"), FACTS, flags))


def c(score, tpe, kind="rivalry"):
    first = "女雙 八強：A（中國，世界 #1）勝 洪恩慈／謝沛珊（中華台北，世界 #10）" if tpe else "女單 決賽：A（韓國）勝 B（日本）"
    return {"kind": kind, "score": score, "event": "WS", "facts": [first + f" {score}", "x"]}


def test_pick_only_one_with_tpe_bonus():
    """10-03 Raymond：一天最多一支；台灣故事 +3 加分，分數最高的那支。"""
    assert [x["score"] for x in story.pick([c(20, False), c(7, True), c(15, False)])] == [20]
    assert [x["score"] for x in story.pick([c(12, True), c(14, False)])] == [12]                   # 12+3 > 14
    assert [x["score"] for x in story.pick([c(14, True), c(10, False)])] == [14]
    assert story.pick([c(5, False)]) == []                                                          # 寧缺勿濫


def test_record_written_as_score_is_allowed():
    """實驗發現：「16 勝 0 負」被寫成「0 比 16」「9-0」會被當成捏造比分。"""
    from brief.llm import unverified
    src = "TAN Ning / LIU Sheng Shu對洪恩慈／謝沛珊 8 戰全勝，局數 16 勝 0 負；從 2025 丹麥公開賽四強起 9 勝 0 負"
    assert unverified("局數 0 比 16，最近 9-0", src, {}) == []
    assert unverified("局數 0 比 15", src, {})                           # 對不上的照擋


def test_cjk_pair_with_event_prefix_and_comeback_tag():
    """粉專試跑：「男單林俊易（LIN Chun-Yi）」不該被當成對照表不符；三局先輸第一局的比賽事實要寫明逆轉。"""
    from brief.llm import unverified
    from brief import storylines as sl
    src = "男單 決賽：林俊易（LIN Chun-Yi）（中華台北）勝 Yushi TANAKA（日本），比分 11-21 21-17 21-14"
    assert unverified("男單林俊易（LIN Chun-Yi）11-21 21-17 21-14 奪冠", src, {"林俊易": "LIN Chun-Yi"}) == []
    assert unverified("男單林俊易（LIN Dan）奪冠", src, {"林俊易": "LIN Chun-Yi"})
    side = lambda n: {"name": n, "country": "TPE", "pairing_id": 1, "home": True}
    m = {"event": "MS", "round": "Final", "winner": side("A"), "loser": side("B"), "winner_rank": None, "loser_rank": None,
         "score": "11-21 21-17 21-14"}
    assert "先輸第一局逆轉" in sl.match_fact(m)
    assert "逆轉" not in sl.match_fact({**m, "score": "21-11 17-21 21-14"})


def test_cjk_pair_with_rank_inside_parentheses():
    from brief.llm import unverified
    src = "女單 64 強：邱品蒨（CHIU Pin-Chian）（中華台北，世界 #17）勝 X（馬來西亞，世界 #28），比分 21-13 21-13"
    assert unverified("女單邱品蒨（CHIU Pin-Chian，世界 #17）21-13 21-13 晉級", src, {"邱品蒨": "CHIU Pin-Chian"}) == []


def test_english_only_name_when_chinese_exists():
    from brief.llm import unverified
    src = "男單 決賽：林俊易（LIN Chun-Yi）（中華台北）勝 Yushi TANAKA（日本），比分 11-21 21-17 21-14"
    assert "LIN Chun-Yi（應寫中文名林俊易）" in unverified("LIN Chun-Yi 奪冠", src, {"林俊易": "LIN Chun-Yi"})
    assert unverified("林俊易（LIN Chun-Yi）奪冠", src, {"林俊易": "LIN Chun-Yi"}) == []


def test_fbpost_hashtag_without_space():
    from brief import fbpost
    body = ("【羽球故事】從 2025 丹麥公開賽四強起，AN Se Young 對 Akane YAMAGUCHI 9 勝 0 負。"
            + "曾經被壓著打的那幾年，她一場一場追回來，到現在換成對手找不到答案。" * 16 + "\n\n" + fbpost.SIGNATURE)
    facts = ["AN Se Young對Akane YAMAGUCHI；從 2025 丹麥公開賽四強起 9 勝 0 負"]
    bad = fbpost.check({"body": body, "hashtags": ["#羽球", "#AN Se Young", "#亞運"]}, facts)
    assert any("空格" in p for p in bad)
    assert not fbpost.check({"body": body, "hashtags": ["#羽球", "#安洗瑩", "#亞運"]}, facts)


def test_dates_are_not_scores():
    """日期「2026-10-01」不能被讀成比分 26-10，讓自算的 26 名混過去（粉專試跑：68−42=26）。"""
    from brief.llm import SCORE, unverified
    assert SCORE.findall("今天是 2026-10-01") == []
    assert SCORE.findall("比分 19-21 21-13，21 比 18") == [("19", "21"), ("21", "13"), ("21", "18")]
    src = "今天是 2026-10-01；女單決賽：A（世界 #68）勝 B（世界 #42），比分 22-20 15-21 21-14"
    assert "26" in unverified("排名比對手低 26 名", src, {})


def test_leading_zero_numbers():
    from brief.llm import unverified
    assert unverified("10 月 1 日", "今天是 2026-10-01", {}) == []


def test_v2_length_and_structure():
    """notes 10-02 18:55 a：口播上限 300（10-03 由 260 放寬）（短於 200 要寫理由）；結尾順序 價值段 → 伏筆 → 留言問題；value_kind 必填。"""
    flags = story._flags(FACTS)
    assert any("超過上限 300" in p for p in story.check(out(VOICE * 2), FACTS, flags))
    short = VOICE[:60]
    assert any("short_reason" in p for p in story.check(out(short), FACTS, flags))
    assert not any(p.startswith("口播") for p in story.check(out(short, short_reason="這站只有一場可講"), FACTS, flags))
    o = out(VOICE)
    o["segments"].reverse()
    assert "最後一段要是留言問題" in story.structure_problems(o)
    assert any("value_kind" in p for p in story.structure_problems({**out(VOICE), "value_kind": ""}))
    hooked = out(VOICE, hook={"branch": "B1", "teaser": "他們還有一個剋星。"})
    assert any("伏筆" in p for p in story.structure_problems(hooked))
    hooked["segments"].insert(2, {"part": "伏筆", "voice": "他們還有一個剋星。"})
    assert story.structure_problems(hooked) == []


def test_system_prompt_uses_v2():
    sp = story.system_prompt()
    assert "說書人" in sp and "200–300" in sp and "價值段" in sp and "提到名字的句子（含留言問題、伏筆句）都要引用" in sp
    assert "純口播文字" not in sp                     # v2 的「輸出格式」由程式的 JSON 取代


def test_too_long_retry_sends_previous_draft_to_trim():
    """10-03：只因太長被退時，把上一版附給寫手刪減（不是從頭重寫）；最多 3 次。"""
    import json

    class Fake:
        def __init__(self, outs):
            self.outs, self.prompts = list(outs), []

        def complete(self, system, user):
            self.prompts.append(user)
            return self.outs.pop(0)

    long_out = {**out(VOICE * 2), "segments": [{**seg, "sentences": []} for seg in out(VOICE * 2)["segments"]]}
    llm = Fake([json.dumps(long_out, ensure_ascii=False)] * 3)
    res, problems = story.write(llm, "u", FACTS)
    assert res is None and len(llm.prompts) == 3 and "超過上限 300" in problems[0]
    assert "在這一版上刪減" in llm.prompts[1] and VOICE[:20] in llm.prompts[1] and "280" in llm.prompts[1]
