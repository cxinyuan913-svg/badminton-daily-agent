"""外國選手譯名表測試：句子取自中央社、NOWnews 真實報導（tests/fixtures/foreign_name_sentences.json）。"""
import json
from pathlib import Path

from brief import crawler, digest, foreign_names as fn, llm

FIX = json.loads((Path(__file__).parent / "fixtures" / "foreign_name_sentences.json").read_text(encoding="utf-8"))


def db():
    con = crawler.connect(":memory:")
    con.executescript(fn.TABLE)
    con.executemany("INSERT INTO player (player_id, name_display, country_code) VALUES (?, ?, ?)", FIX["players"])
    return con


def load_all(con):
    for s in FIX["sentences"]:
        fn.store(con, fn.extract(con, s["text"], s["url"], s["source"]), s["url"], s["source"])


def test_extract_and_clean_from_real_sentences():
    con = db()
    load_all(con)
    got = dict(con.execute("SELECT name_en, name_zh FROM foreign_name"))
    assert got["Kunlavut VITIDSARN"] == "昆拉武特" and got["LOH Kean Yew"] == "駱建佑"
    assert got["Alwi FARHAN"] == "法漢"                                   # 「的法漢（Alwi Farhan）」去掉助詞
    assert got["Leo Rolly CARNANDO"] == "卡爾南多"                         # 「劉毅迎戰卡爾南多」去掉動詞
    assert got["Daniel MARTHIN"] == "馬丁"                                 # 「與馬丁」
    assert fn.clean_zh("韓國姜敏赫") == "姜敏赫" and fn.clean_zh("遭韓國全奕陳") == "全奕陳"


def test_confirmed_needs_two_sources():
    con = db()
    load_all(con)
    status = dict(con.execute("SELECT name_en, status FROM foreign_name"))
    assert status["Kunlavut VITIDSARN"] == "confirmed"                     # 中央社＋NOWnews
    assert status["LOH Kean Yew"] == "candidate"                           # 只有 NOWnews


def test_cna_wins_conflicts_and_other_kept_as_evidence():
    con = db()
    fn.store(con, [(58089, "法罕", "…印尼法罕（Alwi Farhan）…")], "https://www.cna.com.tw/x", "cna")
    fn.store(con, [(58089, "法漢", "…印尼法漢（Alwi Farhan）…")], "https://www.nownews.com/y", "nownews")
    name, status, ev = con.execute("SELECT name_zh, status, evidence FROM foreign_name WHERE player_id=58089").fetchone()
    assert name == "法罕" and status == "candidate" and "另見「法漢」" in ev     # 兩個網站寫法不同：不算同一譯名的兩個來源


def test_no_match_without_unique_full_name():
    con = db()
    assert fn.match_player(con, "Kunlavut") is None                        # 只有一個字
    assert fn.match_player(con, "Kunlavut Vitidsarn") == 64032
    assert fn.match_player(con, "Chou Tien Chen") is None                  # 台灣選手不進外國譯名表


def test_digest_uses_confirmed_foreign_name_and_llm_checks_it():
    con = db()
    load_all(con)
    a = crawler.pairing_id(con, [64032])
    assert digest._side(con, a)["name"] == "昆拉武特"
    b = crawler.pairing_id(con, [76115])
    assert digest._side(con, b)["name"] == "LOH Kean Yew"                  # 還是 candidate → 英文
    src = "- 男單 決賽：**昆拉武特**（泰國，#1）勝 LOH Kean Yew（新加坡，#13） 21-12 21-16"
    assert llm.unverified("昆拉武特直落二奪冠", src, {"昆拉武特": "Kunlavut VITIDSARN"}) == []
    assert llm.unverified("法漢晉級", src, {"法漢": "Alwi FARHAN"}) == ["法漢"]   # 譯名在表上但原始資料沒有


def pinyin_db():
    con = db()
    con.executemany("INSERT INTO player (player_id, name_display, country_code) VALUES (?, ?, ?)", [
        (1, "WANG Chang", "CHN"), (2, "LIANG Wei Keng", "CHN"), (3, "YIN Yi Qing", "CHN"),
        (4, "CHENG Shu", "CHN"), (5, "HONG Wei", "CHN"), (6, "LIU Yi", "CHN")])
    return con


def test_pinyin_link_needs_evidence_and_strict_rules():
    """華裔選手：新聞原文的漢字名轉拼音對回 BWF 名字。NOWnews 2026-09-24 原句：「梁偉鏗與王昶面對印尼頭號男雙」。"""
    con = pinyin_db()
    text = "第二點男雙，梁偉鏗與王昶面對印尼頭號男雙阿爾菲安。中國隊在2：0領先下連丟三盤。"
    got = {(p, w) for p, w, _ in fn.extract_pinyin(con, text)}
    assert got == {(2, "梁偉鏗"), (1, "王昶")}
    assert fn.extract_pinyin(con, "梁偉鏗與王昶面對對手") == []              # 文章沒寫到「中國」→ 不連
    noise = "中國選手因疫情影響，打法成熟，葉宏蔚也表示相遇不意外。"
    assert fn.extract_pinyin(con, noise) == []                                # 不是姓開頭、或是台灣選手名字的一部分
    assert fn.extract_pinyin(con, "中國劉毅迎戰印尼") == []                   # 兩字名前後沒有分隔字 → 不收
    assert {w for _, w, _ in fn.extract_pinyin(con, "中國的何濟霆與劉毅迎戰印尼")} == {"劉毅"}
    assert fn.clean_zh("福島由紀") == "福島由紀" and fn.clean_zh("頭號男雙阿爾菲安") == "阿爾菲安"
