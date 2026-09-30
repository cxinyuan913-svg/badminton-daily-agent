"""日報中文化測試：對照表、輪次、項目、國家、賽事名稱、選手中文名。"""
import datetime as dt

from brief import crawler, digest, llm, zh
from tests.test_digest import db


def test_event_and_round():
    assert [zh.event(e) for e in ["MS", "WS", "MD", "WD", "XD"]] == ["男單", "女單", "男雙", "女雙", "混雙"]
    assert [zh.round_name(r) for r in ["R64", "R32", "R16", "QF", "SF", "F", "Final"]] == \
        ["64 強", "32 強", "16 強", "八強", "四強", "決賽", "決賽"]
    assert zh.round_name("Qual. R32") == "資格賽 32 強"
    assert zh.round_name("R2") == "第 2 輪"             # 團體賽小組賽
    assert zh.round_name("Round Robin") == "Round Robin"  # 查不到保留原文


def test_status_country_level():
    assert zh.status("Retired") == "退賽" and zh.status("Walkover") == "不戰而勝"
    assert zh.country("TPE") == "中華台北" and zh.country("KOR") == "韓國" and zh.country("HKG") == "香港"
    assert zh.country("INA/MAS") == "印尼／馬來西亞"      # 跨國雙打
    assert zh.country("XYZ") == "XYZ"
    assert zh.level("IC") == "國際挑戰賽" and zh.level("S1000") == "超級 1000"


def test_tournament_names():
    assert zh.tournament("VICTOR China Open 2026") == "2026 中國公開賽"
    assert zh.tournament("VICTOR China Masters 2026") == "2026 中國大師賽"     # Masters 不能被當成 Open
    assert zh.tournament("YONEX All England Open Badminton Championships 2026") == "2026 全英賽"
    assert zh.tournament("HSBC BWF World Tour Finals 2026") == "2026 年終總決賽"
    assert zh.tournament("BWF World Championships 2026") == "2026 世界錦標賽"
    assert zh.tournament("20th Asian Games Aichi-Nagoya 2026 (Individual)") == "2026 亞運"
    assert zh.tournament("MAXX North Harbour International 2026") == "MAXX North Harbour International 2026"


def test_player_table_is_valid_and_applied():
    rows = zh.load_player_table()
    assert len({r["player_id"] for r in rows}) == len(rows)
    assert all(r["name_zh"] and r["name_en"] for r in rows)
    con = crawler.connect(":memory:")
    con.execute("INSERT INTO player (player_id, name_display) VALUES (34810, 'CHOU Tien Chen')")
    assert zh.apply_player_names(con) == 1
    assert zh.apply_player_names(con) == 0                 # 重跑不重複更新
    assert con.execute("SELECT name_zh FROM player WHERE player_id=34810").fetchone()[0] == "周天成"
    assert zh.player("CHOU Tien Chen", "周天成") == "周天成（CHOU Tien Chen）"
    assert zh.player("Sirui LU", None) == "Sirui LU"


def test_digest_is_chinese():
    con = db()
    text, matches, _, _ = digest.build(con, dt.date(2026, 10, 1))
    assert "（超級 100）" in text and "64 強" in text
    for m in matches:
        assert m["event"] in zh.EVENT                       # 資料本身維持代碼，只有輸出轉中文
    line = digest._line({**matches[0], "status": "Retired"})
    assert "（退賽）" in line and ("男單" in line or "混雙" in line)
    assert "紐西蘭" in line or "（" in line


ZH_TABLE = {"周天成": "CHOU Tien Chen", "王齊麟": "WANG Chi-Lin"}
SOURCE = "- 男單 八強：**周天成（CHOU Tien Chen）**（中華台北，#12）勝 Viktor AXELSEN（丹麥，#3） 21-15 21-19"


def test_fact_check_with_chinese_names():
    ok = "周天成（CHOU Tien Chen）以 21-15 21-19 擊敗世界第 3 的 Viktor AXELSEN。"
    assert llm.unverified(ok, SOURCE, ZH_TABLE) == []
    swapped = "王齊麟（CHOU Tien Chen）晉級四強。"                  # 中文與英文對不上
    assert "王齊麟（CHOU Tien Chen）對照表不符" in llm.unverified(swapped, SOURCE, ZH_TABLE)
    invented = "安賽龍（Viktor AXELSEN）止步八強。"                   # 自行翻譯，對照表沒有
    assert "安賽龍（Viktor AXELSEN）對照表不符" in llm.unverified(invented, SOURCE, ZH_TABLE)
    absent = "王齊麟今天沒有出賽。"                                   # 對照表裡的人，但原始資料沒有
    assert llm.unverified(absent, SOURCE, ZH_TABLE) == ["王齊麟"]


def test_news_gist_only_for_english_sources():
    from brief import news
    from pathlib import Path

    class FakeLLM:
        calls = []

        def complete(self, system, user):
            self.calls.append(user)
            return "世青賽即將在非洲開打\n多的一行"
    con = db()
    page = (Path(__file__).parent / "fixtures" / "bwf_news_2026-09-30.html").read_text(encoding="utf-8")
    news.store(con, news.parse("bwf", page))
    text, _, _, items = digest.build(con, dt.date(2026, 9, 30), llm=FakeLLM())
    assert items[0]["gist"] == "世青賽即將在非洲開打"
    assert "【BWF】World Juniors: Africa Beckons" in text and "重點：世青賽即將在非洲開打" in text


def test_official_list_and_doubles_display():
    """2026-09-30：正式名單 config/players_zh.csv；雙打兩人都有中文名時寫「王齊麟／李哲輝」。"""
    rows = {r["player_id"]: r for r in zh.load_player_table()}
    assert zh.official_table_exists() and rows["34810"]["name_zh"] == "周天成"
    assert all(r["track"] in ("Y", "N") for r in rows.values())
    con = crawler.connect(":memory:")
    for pid, en in [(96514, "WANG Chi-Lin"), (99102, "LEE Jhe-Huei"), (1, "Viktor AXELSEN")]:
        con.execute("INSERT INTO player (player_id, name_display, country_code) VALUES (?, ?, ?)",
                    (pid, en, "DEN" if pid == 1 else "TPE"))
    zh.apply_player_names(con)
    both = crawler.pairing_id(con, [96514, 99102])
    mixed = crawler.pairing_id(con, [96514, 1])
    assert digest._side(con, both)["name"] == "王齊麟／李哲輝"
    assert digest._side(con, mixed)["name"] == "Viktor AXELSEN / 王齊麟（WANG Chi-Lin）"
