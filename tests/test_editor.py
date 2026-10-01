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
    writer = Fake([script(GOOD, R1_BAD), script(GOOD, "兩人交手 99 場。"), script(GOOD, "兩人交手 98 場。")])   # 重寫最多兩次
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
    real = [r["no"] for r in fbpost.checked_rules()]             # notes 21:00b：Raymond 勾 1、4–12，2、3 不用
    assert real == [1, 4, 5, 6, 7, 8, 9, 10, 11, 12]


def test_post_length_ranges_2110():
    """notes 21:10：故事貼文、台灣戰報 500–1,800 字；冷知識 300–1,200 字。"""
    assert fbpost.body_range("taiwan") == (500, 1800) and fbpost.body_range("story") == (500, 1800)
    assert fbpost.body_range("history") == (300, 1200) and fbpost.body_range("rules") == (300, 1200)
    body = "昨天台灣選手對外國選手 7 勝 2 負。" + "這一組在場上一直找不到節奏，但最後一局咬得很緊。" * 18   # 約 430 字
    tags = ["#羽球", "#亞運", "#周天成"]
    facts = ["昨天台灣選手對外國選手 7 勝 2 負（共 9 場；台灣內戰另計）"]
    length = lambda kind: [p for p in fbpost.check({"body": body, "hashtags": tags}, facts, kind, gap=1) if "字，不在" in p]
    assert length("taiwan") and not length("history")            # 430 字：戰報太短、冷知識可以


def test_fb_form_rules_r5_r6():
    """R5：簽名、不用 Markdown／花體字；R6：超過 1 天要故事語氣。"""
    sig = fbpost.SIGNATURE
    news = "【亞運羽球】洪恩慈／謝沛珊又碰上世界第一。\n\n" + sig
    assert fbpost.check_form(news, gap=1) == []
    assert any("故事語氣" in p for p in fbpost.check_form(news, gap=3))
    assert fbpost.check_form("【羽球故事】她們和世界第一的 8 次交手。\n\n" + sig, gap=3) == []
    assert any("簽名" in p for p in fbpost.check_form("【羽球故事】沒有簽名", gap=3))
    assert any("Markdown" in p for p in fbpost.check_form("【羽球故事】**粗體**\n\n" + sig, gap=3))
    assert any("花體" in p for p in fbpost.check_form("【羽球故事】\U0001D400\U0001D401\n\n" + sig, gap=3))
    assert fbpost.tone_for(None) == "story" and fbpost.tone_for(0) == "news"


def test_missing_tpe_chinese_name_listed():
    facts = ["3 年前的今天（2023-10-01），2023 高雄大師賽 女單 決賽：LIANG Ting Yu（中華台北，世界 #68）勝 Riko GUNJI（日本），比分 22-20",
             "男單 決賽：林俊易（LIN Chun-Yi）（中華台北，世界 #24）勝 Yushi TANAKA（日本），比分 11-21 21-17 21-14"]
    assert fbpost.missing_tpe_zh(facts) == ["LIANG Ting Yu"]


def test_budget_alert_per_job():
    """同一天腳本已經告警過，粉專被擋也要告警（notes 21:00）。"""
    import sqlite3
    from brief import script
    con = sqlite3.connect(":memory:")
    alerts = []
    script.budget_alert(con, alerts.append, "2026-10-02")
    script.budget_alert(con, alerts.append, "2026-10-02", who="粉專貼文")
    script.budget_alert(con, alerts.append, "2026-10-02", who="粉專貼文")
    assert len(alerts) == 2 and "粉專貼文" in alerts[1]


def test_r7_number_budget():
    """R7：比分一串算 1 個、年份與【】不算；一篇最多 5 個、一段最多 1 個。"""
    assert fbpost.count_numbers("【2026 亞運】決賽 19-21 21-13 21-18 逆轉") == 1
    assert fbpost.count_numbers("世界 #46 打敗世界第 1，打了 111 分鐘") == 3
    sig = fbpost.SIGNATURE
    ok = "【羽球故事】2026 亞運，排名四十多名的組合。\n\n決賽第三局 21-18 收下。\n\n" + sig
    assert not [p for p in fbpost.check_form(ok, gap=2) if "數字" in p]
    crowded = "【羽球故事】世界 #46 打敗世界第 1。\n\n" + sig
    assert not any("一段" in p for p in fbpost.check_form(crowded, gap=2))   # 05:55 C：每段的數字交給編輯判斷
    many = "【羽球故事】開頭。\n\n" + "\n\n".join(f"第 {i} 段" for i in range(1, 8)) + "\n\n" + sig
    assert any("超過 5 個" in p for p in fbpost.check_form(many, gap=2))


R9_BAD = "女單黃宥薰、男雙劉廣珩／陳政寬、混雙李佳馨／吳冠勳也都打進四強，對手全是後來的冠亞軍。"


def test_r9_counterexample_goes_back_to_writer():
    """notes 23:10：R9 反例（四強輸給冠亞軍＝廢話），編輯判不通過就交回寫手。"""
    r9 = "KIM Ga Eun 這屆停在 16 強，輸給的 WANG Zhi Yi 後來也一路贏到最後。"   # 名字都在事實清單，只差 R9
    writer = Fake([script(GOOD, r9), script(GOOD)])
    editor = Fake([json.dumps({"items": [{"rule": "R9", "pass": False, "quote": r9, "comment": "冠軍本來就一路贏到最後，刪掉"}]},
                              ensure_ascii=False), verdict()])
    out, _ = story.generate(writer, FACTS, editor=editor)
    assert out["editor"]["first"][0]["rule"] == "R9" and out["editor"]["rewritten"]
    assert "R9 不通過" in writer.prompts[1][1]
    assert "換成「當然」開頭" in story.EDITOR_SYSTEM and "R10" in story.EDITOR_SYSTEM and "R11" in story.EDITOR_SYSTEM


def test_r10_counts_sides():
    from brief import storylines as sl
    facts = ["女雙 八強：TAN Ning / LIU Sheng Shu（中國，世界 #1）勝 洪恩慈／謝沛珊（中華台北，世界 #10），比分 21-15 21-15",
             "女單 四強：Tanvi SHARMA（印度，世界 #34）勝 黃宥薰（HUANG Yu-Hsun）（中華台北，世界 #24），比分 21-17 21-11",
             "男雙 四強：Aaron CHIA / Aaron TAI（馬來西亞，世界 #378）勝 劉廣珩／陳政寬（中華台北，世界 #82），比分 24-26 21-19 21-16",
             "混雙 四強：A B（日本）勝 李佳馨／吳冠勳（中華台北），比分 21-19 21-8"]
    assert sl.count_sides("洪恩慈／謝沛珊對上 TAN Ning / LIU Sheng Shu", facts) == ["TAN Ning / LIU Sheng Shu", "洪恩慈／謝沛珊"]
    many = sl.count_sides("洪恩慈／謝沛珊輸給 TAN／LIU；" + R9_BAD + "CHIA 也贏了", facts)
    assert len(many) == 6                                        # 超過 4 組 → R10 退回


def test_r11_related_trivia_only_when_relevant():
    hist = ["上一屆（2025 日本公開賽）也是這一組拿冠軍：這站是衛冕戰"]
    got = story.related_trivia(hist)
    assert got and "第 6 條" in got[0]                          # 衛冕 → 積分在下一屆開打時失效
    assert story.related_trivia(["兩人交手 10 場"]) == []          # 不相關就不硬塞


def test_r11_recent_news_matches_names(tmp_path):
    import sqlite3
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE news_item (url TEXT PRIMARY KEY, source TEXT, title TEXT, published TEXT, fetched_at TEXT)")
    con.execute("CREATE TABLE foreign_article (url TEXT PRIMARY KEY, source TEXT, text TEXT, fetched_at TEXT)")
    con.execute("INSERT INTO news_item VALUES ('u1', 'cna', '周天成亞運賽後受訪', '2026-09-28', NULL)")
    con.execute("INSERT INTO foreign_article VALUES ('u1', 'cna', '前言。周天成說膝蓋還有點緊。他會調整賽程。最後一句。', NULL)")
    con.execute("INSERT INTO news_item VALUES ('u2', 'cna', '舊新聞', '2026-07-01', NULL)")
    con.execute("INSERT INTO foreign_article VALUES ('u2', 'cna', '周天成很久以前的新聞。', NULL)")
    got = story.recent_news(con, ["周天成（CHOU Tien Chen）"], "2026-10-01")
    assert len(got) == 1 and "周天成亞運賽後受訪（cna，2026-09-28）" in got[0] and "膝蓋" in got[0]
    assert story.recent_news(con, ["林俊易"], "2026-10-01") == []


def test_examples_sanitized_names():
    """05:55 A：範例檔不動，載入時未 confirmed 的外國選手中文名換回英文。"""
    text = fbpost.sanitize_names("印尼的卡納多／馬汀（Leo Rolly CARNANDO／Daniel MARTHIN），決賽贏過坤拉武特與白荷娜。")
    assert "卡納多" not in text and "馬汀" not in text and "白荷娜" not in text
    assert "Leo Rolly CARNANDO／Daniel MARTHIN（Leo Rolly CARNANDO／Daniel MARTHIN）" not in text   # 重複括號去掉
    assert "BAEK Ha Na" in text


def test_fbpost_pushes_once_per_day(monkeypatch, tmp_path):
    """切換主機：手動觸發推過 10-02，07:00 的排程就不能再推一次。"""
    import sqlite3
    con = sqlite3.connect(":memory:")
    monkeypatch.setattr(fbpost, "_env", lambda k: {"FBPOST": "1", "DISCORD_WEBHOOK_FBPAGE": "https://hook/fb"}.get(k))
    monkeypatch.setattr(fbpost, "OUT_DIR", tmp_path)
    monkeypatch.setattr(fbpost, "choose", lambda con, today: ("history", ["今天是 2026-10-02"], {"gap": None}))
    body = "【羽球故事】" + "三年前的今天，高雄同一天出現兩位台灣單打冠軍，是很值得回味的一天。" * 10 + "\n\n" + fbpost.SIGNATURE
    monkeypatch.setattr(fbpost, "generate", lambda *a, **k: ({"body": body, "hashtags": ["#羽球", "#台灣", "#冠軍"]}, []))
    sent = []
    assert fbpost.run(con, "2026-10-02", make_llm=lambda: None, send=sent.append, ignore_budget=True)["status"] == "ok"
    assert fbpost.run(con, "2026-10-02", make_llm=lambda: None, send=sent.append, ignore_budget=True)["status"] == "skipped_sent"
    assert len(sent) == 1
    assert fbpost.run(con, "2026-10-03", make_llm=lambda: None, send=sent.append, ignore_budget=True)["status"] == "ok"


def test_prompt_really_contains_examples():
    """10-02 修 bug：範例那段少了 f 前綴，模型收到的是字面上的 {_examples_fb(con)}。"""
    writer = Fake([json.dumps({"body": ""}, ensure_ascii=False)] * 2)
    fbpost.generate(writer, "story", ["事實"], gap=3)
    prompt = writer.prompts[0][1]
    assert "{_examples_fb" not in prompt and "範例 1" in prompt


def test_theme_requires_every_story_and_skips_r10():
    theme = [{"title": "男雙世界第一", "keys": ["KIM Won Ho"]}, {"title": "女雙世界第一", "keys": ["TAN Ning"]}]
    body = "【羽球故事】" + "KIM Won Ho 四強就輸了。" * 30 + "\n\n" + fbpost.SIGNATURE
    probs = fbpost.check({"body": body, "hashtags": ["#羽球", "#亞運", "#男雙"]}, ["KIM Won Ho"], "story", 3, theme)
    assert any("沒講到：女雙世界第一" in p for p in probs)
    writer = Fake([json.dumps({"body": ""}, ensure_ascii=False)] * 2)
    fbpost.generate(writer, "story", ["事實"], gap=3, theme=theme)
    assert "每一個都要講到" in writer.prompts[0][1] and "2. 女雙世界第一" in writer.prompts[0][1]
