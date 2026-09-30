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
