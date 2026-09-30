"""LLM 介面與事實檢查測試（不連網，用假的 LLM）。"""
import datetime as dt

from brief import digest, llm
from tests.test_digest import db


class FakeLLM:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def complete(self, system, user):
        self.calls.append((system, user))
        return self.reply


SOURCE = "- MS R64：**Sirui LU**（NZL，無排名）勝 Alexander LEE（NZL，#12） 21-15 18-21 21-19"


def test_unverified_flags_numbers_and_names_not_in_source():
    assert llm.unverified("Sirui LU 以 21-15 18-21 21-19 擊敗第 12 名的 Alexander LEE", SOURCE) == []
    assert llm.unverified("Sirui LU 苦戰 3 局、耗時 58 分鐘擊敗 Viktor AXELSEN", SOURCE) == ["3", "58", "Viktor AXELSEN"]
    assert llm.unverified("男單 MS 八強 QF", SOURCE) == []
    assert llm.unverified("決勝局 21 比 19 收下", SOURCE) == []           # 比分拆開寫也查得到
    assert llm.unverified("連勝 5 場", SOURCE) == ["5"]                    # 不能被 21-15 的「5」矇混


def test_highlight_marks_ai_and_lists_unverified():
    out = llm.highlight(FakeLLM("Sirui LU 爆冷擊敗世界第 12 名，連續 5 場勝利。"), SOURCE)
    assert out.startswith("**今日重點**（AI 整理，請審稿）")
    assert "⚠️待確認（原始資料查不到）：5" in out


def test_digest_with_llm_keeps_facts_and_survives_llm_failure():
    con = db()
    fake = FakeLLM("今天 North Harbour 首日結束。")
    text, matches, _, _ = digest.build(con, dt.date(2026, 10, 1), llm=fake)
    assert text.splitlines()[1].startswith("**今日重點**")
    assert "MAXX North Harbour International 2026" in fake.calls[0][1]      # LLM 只拿到事實摘要
    assert "⚠️待確認" not in text                                          # 內容都查得到

    class Broken:
        def complete(self, system, user):
            raise TimeoutError("api down")
    errors = []
    text2, matches2, _, _ = digest.build(con, dt.date(2026, 10, 1), llm=Broken(), errors=errors)
    assert matches2 == matches and "今日重點" not in text2
    assert errors[0].startswith("llm:")


def test_no_llm_call_when_nothing_new():
    con = db()
    fake = FakeLLM("x")
    digest.build(con, dt.date(2026, 12, 31), llm=fake)
    assert fake.calls == []


def test_model_routing_and_env_override(monkeypatch):
    monkeypatch.delenv("LLM_MODEL_ROUTINE", raising=False)
    monkeypatch.setattr(llm, "ENV_FILE", llm.ENV_FILE.with_name("__no_such_env__"))
    assert llm.model_for("routine") == "claude-sonnet-5-5"
    assert llm.model_for("heavy") == "claude-opus-5-5"
    monkeypatch.setenv("LLM_MODEL_ROUTINE", "claude-haiku-4-5")
    assert llm.model_for("routine") == "claude-haiku-4-5"


def test_usage_logged_with_cost():
    from brief import crawler
    con = crawler.connect(":memory:")
    llm.log_call(con, "routine", "highlight", "claude-sonnet-5-5", 1_000_000, 100_000)
    llm.log_call(con, "heavy", None, "some-unknown-model", 10, 10)
    rows = con.execute("SELECT purpose, task, model, cost_usd FROM llm_call ORDER BY call_id").fetchall()
    assert rows == [("routine", "highlight", "claude-sonnet-5-5", 3.0), ("heavy", None, "some-unknown-model", None)]


# 2026-09-30 實跑 claude-sonnet-5-5 的真實輸出（節錄），當成回歸測試
REAL_SOURCE = ("- 男單 八強：**Alwi FARHAN**（印尼，#10）勝 周天成（CHOU Tien Chen）（中華台北，#5） 21-10 21-11\n"
               "- 混雙 四強：**WEI Ya Xin / JIANG Zhen Bang**（中國，#3）勝 Nicole Gonzales CHAN / YE Hong Wei（中華台北，#9） 21-13 21-9")


def test_fabricated_team_score_is_flagged():
    out = "周天成（CHOU Tien Chen）八強以 10-21 11-21 不敵 Alwi FARHAN。團體賽方面，男團決賽印尼 3–2 中國，女團決賽中國 3–0 日本。"
    missing = llm.unverified(out, REAL_SOURCE, {"周天成": "CHOU Tien Chen"})
    assert missing == ["3-2", "3-0"]                                       # 10-21 11-21 是敗方角度，正確


def test_score_written_as_bi_and_standalone_digits():
    assert llm.unverified("混雙四強以 13 比 21、9 比 21 落敗", REAL_SOURCE, {}) == []
    assert llm.unverified("決勝局 21:19 收下", REAL_SOURCE, {}) == ["21-19"]
    assert llm.unverified("世界第 10 名的 Alwi FARHAN", REAL_SOURCE, {}) == []


def test_self_correction_output_is_rejected():
    class Rambling:
        def complete(self, system, user):
            return "周天成八強止步。\n\n等一下，上面周天成那句我寫錯了，以下是修正後的完整版本：\n\n周天成八強止步。"
    import pytest
    with pytest.raises(ValueError, match="格式異常"):
        llm.highlight(Rambling(), REAL_SOURCE)
