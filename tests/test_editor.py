"""編輯檢查（notes 20:35）：用固定的假 LLM 測流程，不呼叫 API。三個反例來自 docs/video/review-guidelines.md。"""
import json
from pathlib import Path

import pytest

from brief import fbpost, story

FACTS = ["賽事：2026 亞運（綜合運動會），今天是當地 2026-09-26",
         "女單 16 強：WANG Zhi Yi（中國，世界 #2）勝 KIM Ga Eun（韓國，世界 #12），比分 21-14 11-6",
         "WANG Zhi Yi對KIM Ga Eun交手紀錄 6 勝 4 負（2017 年以來，共 10 場）",
         "前 4 場WANG Zhi Yi 1 勝 3 負；從 2024 丹麥公開賽16 強起 5 勝 1 負",
         "KIM Ga Eun輸的局裡最接近的一局：2025 印尼公開賽16 強第 2 局，19-21（KIM Ga Eun角度）",
         "男單 32 強：周天成（CHOU Tien Chen）（中華台北，世界 #5）勝 Ayman Ibn JAMAN（孟加拉，世界 #382），比分 21-1 21-10"]
# 三個反例（R1 沒交代場景、R2 轉折沒理由、R3 段落跟主軸無關）
R1_BAD = "這 10 場裡最接近的一局，KIM Ga Eun 打到 19 比 21。"
R2_BAD = "轉折在 2024 丹麥公開賽 16 強。從那場起，WANG Zhi Yi 5 勝 1 負。"
R3_BAD = "台灣這邊，周天成以 21 比 1、21 比 10 拿下男單 32 強。"
GOOD = ("WANG Zhi Yi 對 KIM Ga Eun，2017 年以來交手 10 場，6 勝 4 負。前 4 場 WANG Zhi Yi 只贏 1 場，"
        "2024 丹麥公開賽 16 強之後的 6 次交手，WANG Zhi Yi 贏了 5 次。今天亞運 16 強 21 比 14 先拿一局，第二局 11 比 6。") * 2


def script(*voices):
    return json.dumps({"titles": ["a", "b", "c"], "segments": [{"time": "0–60s", "part": "故事", "voice": " ".join(voices), "card": ""}],
                       "todo": []}, ensure_ascii=False)


def verdict(*fails):
    items = [{"rule": r, "pass": r not in dict(fails), "quote": dict(fails).get(r, ""), "comment": "改" if r in dict(fails) else ""}
             for r in ("R1", "R2", "R3")]
    return json.dumps({"items": items}, ensure_ascii=False)


class Fake:
    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []

    def complete(self, system, user):
        self.prompts.append((system, user))
        return self.replies.pop(0)


@pytest.mark.parametrize("bad,rule", [(R1_BAD, "R1"), (R2_BAD, "R2"), (R3_BAD, "R3")])
def test_counterexample_triggers_rewrite(bad, rule):
    writer = Fake([script(GOOD, bad), script(GOOD)])
    editor = Fake([verdict((rule, bad)), verdict()])
    out, problems = story.generate(writer, FACTS, editor=editor)
    assert problems == [] and out["editor"]["rewritten"] and out["editor"]["first"][0]["rule"] == rule
    assert out["editor"]["final"] == []
    assert bad in editor.prompts[0][1]                          # 編輯看到的是整支腳本
    assert f"{rule} 不通過" in writer.prompts[1][1] and bad in writer.prompts[1][1]   # 意見交回寫手


def test_editor_still_failing_keeps_script_with_notes():
    writer = Fake([script(GOOD, R3_BAD), script(GOOD, R3_BAD)])
    editor = Fake([verdict(("R3", R3_BAD)), verdict(("R3", R3_BAD))])
    out, problems = story.generate(writer, FACTS, editor=editor)
    assert out is not None and out["editor"]["final"][0]["rule"] == "R3"         # 不整份丟掉
    md = story.to_markdown("故事 1", {"kind": "rivalry", "final": 10.5}, out)
    assert "編輯意見" in md and R3_BAD in md


def test_rewrite_failing_fact_check_keeps_first_version():
    writer = Fake([script(GOOD, R1_BAD), script(GOOD, "兩人交手 99 場。")])
    editor = Fake([verdict(("R1", R1_BAD))])
    out, _ = story.generate(writer, FACTS, editor=editor)
    assert R1_BAD in out["segments"][0]["voice"] and out["editor"]["final"][0]["rule"] == "R1"


def test_prompts_read_latest_guidelines():
    ids = story.guideline_ids()
    assert ids[:3] == ["R1", "R2", "R3"]
    for prompt in (story.system_prompt(), fbpost.system_prompt()):
        assert "R3 每一段都要跟主軸有關" in prompt and "台灣戰報交給粉專貼文" in prompt
    assert "台灣選手的結果" not in story.system_prompt()        # 交接單 005 的台灣例外已刪除


DB = Path(__file__).resolve().parent.parent / "data" / "brief.db"


@pytest.mark.skipif(not DB.exists(), reason="需要本機的十年回補資料庫")
def test_background_only_same_people():
    """R3：王祉怡 vs 金佳恩的故事，賽果背景不能有周天成、林俊易。"""
    from brief.crawler import connect
    from brief import zh
    con = connect(str(DB))
    zh.apply_player_names(con)
    t = dict(zip(["tournament_id", "name", "level", "start_date", "end_date"], con.execute(
        "SELECT tournament_id, name, level, start_date, end_date FROM tournament WHERE tournament_id=5874").fetchone()))
    cands, daym, allm = story.day_candidates(con, t, "2026-09-26")
    c = story.pick(cands)[0]
    facts = story.materials(con, t, "2026-09-26", c, cands, daym, allm)
    text = "\n".join(facts)
    assert "WANG Zhi Yi" in c["facts"][0] and "周天成" not in text and "林俊易" not in text
    assert any("第 " in f and " 局" in f for f in facts if "最接近" in f) or not any("最接近" in f for f in facts)  # R1


def test_trivia_rules_only_checked(tmp_path):
    p = tmp_path / "rules.md"
    p.write_text("# x\n\n## [ ] 1. 沒審\n- 一句話：不准用\n\n## [x] 2. 審過\n- 一句話：可以用\n- 內容：好\n", encoding="utf-8")
    rules = fbpost.load_trivia_rules(p)
    assert [(r["no"], r["checked"]) for r in rules] == [(1, False), (2, True)]
    assert [r["no"] for r in fbpost.checked_rules(p)] == [2]
    assert fbpost.checked_rules() == []                          # 目前 config/trivia_rules.md 全是 [ ]


def test_taiwan_report_allows_500():
    body = "昨天台灣選手對外國選手 7 勝 2 負。" * 26                  # 約 450 字
    tags = ["#羽球", "#亞運", "#周天成"]
    facts = ["昨天台灣選手對外國選手 7 勝 2 負（共 9 場；台灣內戰另計）"]
    assert not [p for p in fbpost.check({"body": body, "hashtags": tags}, facts, "taiwan") if "正文" in p]
    assert [p for p in fbpost.check({"body": body, "hashtags": tags}, facts, "story") if "正文" in p]
