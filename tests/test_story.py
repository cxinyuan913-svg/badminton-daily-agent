"""故事引擎（交接單 005）：選題、推測句不進事實檢查、推測要有待查清單。"""
from brief import story

FACTS = ["賽事：2026 亞運（綜合運動會），今天是當地 2026-09-29",
         "女單 決賽：AN Se Young（韓國，世界 #1）勝 Akane YAMAGUCHI（日本，世界 #3），比分 21-17 21-9",
         "AN Se Young對Akane YAMAGUCHI交手紀錄 23 勝 15 負（2017 年以來，共 38 場）",
         "AN Se Young目前對Akane YAMAGUCHI連勝 9 場"]
VOICE = ("曾經輸多贏少，現在連贏 9 場。AN Se Young 對 Akane YAMAGUCHI，2017 年以來交手 38 場，AN Se Young 23 勝 15 負。"
         "今天亞運決賽 21 比 17、21 比 9 再贏一次，世界第 1 對世界第 3，第二局只讓對手拿 9 分。") * 2


def out(voice, todo=None):
    return {"titles": ["a", "b", "c"], "segments": [{"time": "0–60s", "part": "故事", "voice": voice, "card": ""}],
            "todo": todo or []}


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


def test_pick_one_or_two_with_tpe_preference():
    assert [x["score"] for x in story.pick([c(20, False), c(7, True), c(15, False)])] == [20, 7]   # 7+3 ≥ 9
    assert [x["score"] for x in story.pick([c(20, False), c(5, True)])] == [20]                    # 台灣故事未達門檻
    assert [x["score"] for x in story.pick([c(12, True), c(20, False)])] == [20, 12]
    assert [x["score"] for x in story.pick([c(14, True), c(10, False)])] == [14]                   # 最高分已含台灣
    assert story.pick([c(5, False)]) == []                                                          # 寧缺勿濫


def test_record_written_as_score_is_allowed():
    """實驗發現：「16 勝 0 負」被寫成「0 比 16」「9-0」會被當成捏造比分。"""
    from brief.llm import unverified
    src = "TAN Ning / LIU Sheng Shu對洪恩慈／謝沛珊 8 戰全勝，局數 16 勝 0 負；從 2025 丹麥公開賽四強起 9 勝 0 負"
    assert unverified("局數 0 比 16，最近 9-0", src, {}) == []
    assert unverified("局數 0 比 15", src, {})                           # 對不上的照擋
