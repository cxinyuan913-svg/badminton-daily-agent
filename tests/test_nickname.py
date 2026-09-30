"""暱稱收集測試：用中央社、NOWnews 的真實新聞標題（tests/fixtures/news_nickname_titles.json）。"""
import datetime as dt
import json
from pathlib import Path

from brief import crawler, news, nickname

ITEMS = json.loads((Path(__file__).parent / "fixtures" / "news_nickname_titles.json").read_text(encoding="utf-8"))
TODAY = dt.date(2026, 10, 5)          # 週一
NAMES = {"王齊麟": "96514", "李洋": None, "周天成": "34810", "戴資穎": None, "林俊易": "86114"}


def db():
    con = crawler.connect(":memory:")
    con.executescript(nickname.NICKNAME_TABLE)
    return con


def status(con, nick):
    row = con.execute("SELECT status, occurrences FROM nickname WHERE nickname=?", (nick,)).fetchone()
    return row and tuple(row)


def test_rule_pair_and_personal_nicknames():
    assert nickname.rule_candidates("李洋王齊麟「麟洋配」摘金衛冕", NAMES) == [("麟洋配", ["李洋", "王齊麟"])]
    assert nickname.rule_candidates("小戴復出", NAMES) == [("小戴", ["戴資穎"])]
    assert nickname.rule_candidates("周天成封神之戰", NAMES) == []            # 「封」不是選手名字的字
    assert nickname.rule_candidates("小組賽抽籤", NAMES) == []
    assert nickname.rule_candidates("天神下凡", NAMES) == []                  # 「天」是周天成名字中間的字，不收
    assert nickname.rule_candidates("男雙新搭配", NAMES) == []


def test_auto_needs_cooccurrence_and_two_sources(monkeypatch):
    monkeypatch.setattr(nickname, "players", lambda con=None: NAMES)
    monkeypatch.setattr(nickname, "SEED", [])                               # 不用冷啟動，看規則本身
    con = db()
    cna_only = [i for i in ITEMS if i["source"] == "cna"]
    nickname.scan(con, cna_only[:2], TODAY)
    assert status(con, "麟洋配") == ("candidate", 2)                         # 有同篇全名，但只有 1 個來源、2 次
    nickname.scan(con, ITEMS, TODAY)                                        # 加上 NOWnews
    assert status(con, "麟洋配") == ("auto", 4)
    nickname.scan(con, ITEMS, TODAY)                                        # 重跑不重複計次
    assert status(con, "麟洋配") == ("auto", 4)


def test_no_auto_without_cooccurrence(monkeypatch):
    monkeypatch.setattr(nickname, "players", lambda con=None: NAMES)
    monkeypatch.setattr(nickname, "SEED", [])
    con = db()
    no_full_name = [i for i in ITEMS if "麟洋配" in i["title"] and "王齊麟" not in i["title"]]
    assert {i["source"] for i in no_full_name} == {"cna", "nownews"}
    nickname.scan(con, no_full_name, TODAY)
    assert status(con, "麟洋配") == ("candidate", 3)                         # 兩個來源、3 次，但沒有一篇同時寫全名


class FakeLLM:
    def __init__(self, reply):
        self.reply = reply

    def complete(self, system, user):
        return self.reply


def test_llm_needs_evidence_in_text(monkeypatch):
    monkeypatch.setattr(nickname, "players", lambda con=None: NAMES)
    title = next(i for i in ITEMS if "台灣一姐" in i["title"])
    good = FakeLLM(json.dumps([{"nickname": "台灣一姐", "players": ["戴資穎"],
                                "evidence": "世界羽球「台灣一姐」換宋碩芸 戴資穎：專注復健"}], ensure_ascii=False))
    assert nickname.llm_candidates(good, title["title"], NAMES) == [("台灣一姐", ["戴資穎"], title["title"])]
    fake_evidence = FakeLLM(json.dumps([{"nickname": "球后", "players": ["戴資穎"], "evidence": "球后戴資穎"}], ensure_ascii=False))
    assert nickname.llm_candidates(fake_evidence, title["title"], NAMES) == []    # 證據不在原文
    unknown = FakeLLM(json.dumps([{"nickname": "台灣一姐", "players": ["宋碩芸"],
                                   "evidence": "世界羽球「台灣一姐」換宋碩芸"}], ensure_ascii=False))
    assert nickname.llm_candidates(unknown, title["title"], NAMES) == []          # 選手不在名單
    assert nickname.llm_candidates(FakeLLM("抱歉，我不確定"), title["title"], NAMES) == []


def test_seed_confirmed_is_keyword_and_weekly_report(monkeypatch):
    monkeypatch.setattr(nickname, "players", lambda con=None: NAMES)
    con = db()
    nickname.scan(con, ITEMS, TODAY)
    assert status(con, "麟洋配")[0] == "confirmed"                           # 冷啟動，程式不會改動
    assert news.keyword_pattern(news.keywords(con)).search("麟洋配再度同台")
    con.execute("INSERT INTO nickname (nickname, target_zh, status, evidence) VALUES ('小周', '周天成', 'candidate', '小周晉級')")
    lines = nickname.weekly_lines(con, TODAY)
    assert lines[1].startswith("__**暱稱週報**__") and any("【待確認】小周 → 周天成" in l for l in lines)
    assert not any("麟洋配" in l for l in lines)                             # confirmed 不再列
    assert nickname.weekly_lines(con, TODAY + dt.timedelta(days=1)) == []  # 只有週一


def test_seed_and_scan_share_one_row(monkeypatch):
    monkeypatch.setattr(nickname, "players", lambda con=None: NAMES)
    con = db()
    nickname.scan(con, ITEMS, TODAY)
    assert con.execute("SELECT COUNT(*) FROM nickname WHERE nickname='麟洋配'").fetchone()[0] == 1


def test_scan_new_only_chinese_sources_once(monkeypatch):
    monkeypatch.setattr(nickname, "players", lambda con=None: NAMES)
    con = db()
    news.store(con, [{k: i[k] for k in ("url", "source", "title", "published")} for i in ITEMS]
               + [{"url": "https://bwf/x", "source": "bwf", "title": "Lee and Wang", "published": "2024-08-05"}])
    calls = []

    class Counting:
        def complete(self, system, user):
            calls.append(user)
            return "[]"
    nickname.scan_new(con, TODAY, Counting())
    assert len(calls) == len(ITEMS)                                        # BWF 英文標題不送
    nickname.scan_new(con, TODAY, Counting())
    assert len(calls) == len(ITEMS)                                        # 掃過的不再送
