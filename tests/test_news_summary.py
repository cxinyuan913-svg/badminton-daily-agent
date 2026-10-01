"""晨報新聞摘要（notes 06:20 第 1 點）：用假 LLM 測合併、事實檢查、沒內文只列標題。"""
import json
import sqlite3

from brief import foreign_names, news_summary


class Fake:
    def __init__(self, reply):
        self.reply, self.task, self.prompts = reply, None, []

    def complete(self, system, user):
        self.prompts.append(user)
        return self.reply


def _con():
    con = sqlite3.connect(":memory:")
    con.executescript(foreign_names.TABLE)
    con.executemany("INSERT INTO foreign_article (url, source, text) VALUES (?, ?, ?)",
                    [("u1", "cna", "周天成亞運八強以 10 比 21、11 比 21 輸給印尼法漢，賽後說膝蓋還有點緊。"),
                     ("u2", "nownews", "周天成八強止步，坦言膝蓋不舒服，回國後先休息。")])
    return con


NEWS = [{"url": "u1", "source": "cna", "title": "周天成八強止步", "published": "2026-09-27"},
        {"url": "u2", "source": "nownews", "title": "周天成：膝蓋還有點緊", "published": "2026-09-27"},
        {"url": "u3", "source": "bwf", "title": "Asian Games: Day 3", "published": "2026-09-27"}]


def test_same_event_merged_and_no_text_title_only():
    llm = Fake(json.dumps({"groups": [{"ids": [1, 2], "summary": "周天成亞運八強輸給法漢，賽後說膝蓋還有點緊，回國先休息。"}]},
                          ensure_ascii=False))
    groups = news_summary.summarize(_con(), llm, NEWS)
    assert [len(g["items"]) for g in groups] == [2, 1]
    assert groups[0]["summary"].startswith("周天成") and groups[1]["summary"] is None     # u3 沒內文
    assert llm.task == "news_summary" and "[3]" not in llm.prompts[0]                    # 沒內文的不送給模型


def test_summary_failing_fact_check_falls_back_to_title():
    llm = Fake(json.dumps({"groups": [{"ids": [1, 2], "summary": "周天成八強以 15 比 21 落敗。"}]}, ensure_ascii=False))
    groups = news_summary.summarize(_con(), llm, NEWS[:2])
    assert groups[0]["summary"] is None                                               # 15 查不到 → 只列標題
