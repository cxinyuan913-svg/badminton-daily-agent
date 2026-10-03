"""新聞列表測試：2026-09-30 四個來源的真實列表頁。"""
from pathlib import Path

from brief import crawler, news

FIXDIR = Path(__file__).parent / "fixtures"


def page(name):
    return (FIXDIR / name).read_text(encoding="utf-8")


def test_bwf_main_titles_and_dates_from_url():
    rows = {r["url"]: r for r in news.parse("bwf", page("bwf_news_2026-09-30.html"))}
    r = rows["https://bwfbadminton.com/news-single/2026/09/30/poised-to-make-deep-inroads/"]
    assert (r["title"], r["published"], r["source"]) == ("World Juniors: Africa Beckons", "2026-09-30", "bwf")
    assert len(rows) == 10 and all(r["title"] for r in rows.values())


def test_bwf_world_tour_dedup_and_title():
    rows = news.parse("bwfworldtour", page("bwfworldtour_news_2026-09-30.html"))
    urls = [r["url"] for r in rows]
    assert len(urls) == len(set(urls))
    hit = next(r for r in rows if r["url"].endswith("/2026/09/07/china-masters-gunawan-sets-sights-higher/"))
    assert (hit["title"], hit["published"]) == ("China Masters: Gunawan Sets Sights Higher", "2026-09-07")


def test_taiwan_media_parsed_but_non_badminton_filtered():
    cna = news.parse_cna(page("cna_aspt_2026-09-30.html"))
    now = news.parse_nownews(page("nownews_sport_2026-09-30.html"))
    assert len(cna) > 15 and len(now) > 15
    assert next(r for r in cna if r["url"].endswith("202609300206.aspx"))["published"] == "2026-09-30"
    tennis = next(r for r in now if r["url"].endswith("/6879243"))
    assert tennis["published"] == "2026-09-30 10:50" and tennis["title"].startswith("詹皓晴、謝淑薇")
    # 這天兩站都沒有羽球新聞：網球、射箭、柔道都不能被篩進來
    assert news.parse("cna", page("cna_aspt_2026-09-30.html")) == []
    assert news.parse("nownews", page("nownews_sport_2026-09-30.html")) == []


def test_badminton_keywords():
    assert news.BADMINTON.search("戴資穎晉級中國大師賽8強")
    assert news.BADMINTON.search("亞運羽球男單")
    assert not news.BADMINTON.search("亞運射箭／複合弓男團奪銅")


def test_store_is_idempotent():
    con = crawler.connect(":memory:")
    rows = news.parse("bwf", page("bwf_news_2026-09-30.html"))
    assert news.store(con, rows) == 10
    assert news.store(con, rows) == 0
    assert con.execute("SELECT COUNT(*) FROM news_item").fetchone()[0] == 10


def test_keywords_retired_and_nicknames():
    """2026-09-30：李洋已退休、現任運動部部長 → 不列關鍵字；戴資穎退休但保留；已採用的暱稱加入。"""
    words = news.keywords()
    assert "李洋" not in words and "戴資穎" in words and "羽球" in words
    assert not news.BADMINTON.search("李洋部長視察國訓中心")                 # 非羽球的政策新聞不收
    assert news.BADMINTON.search("李洋出席羽球頒獎典禮")                      # 含「羽球」照收
    con = crawler.connect(":memory:")
    con.execute("CREATE TABLE nickname (nickname TEXT, status TEXT)")
    con.executemany("INSERT INTO nickname VALUES (?, ?)", [("麟洋配", "confirmed"), ("某某神", "candidate")])
    pat = news.keyword_pattern(news.keywords(con))
    assert pat.search("麟洋配再度同台") and not pat.search("某某神今天出賽")


def test_official_player_table_format(tmp_path):
    from brief import zh
    f = tmp_path / "players_zh.csv"
    f.write_text("﻿類型,bwf_player_id,英文名（BWF）,中文名,暱稱,目前排名（前100）,追蹤（Y/N）,備註\n"
                 "選手,34810,CHOU Tien Chen,周天成,,MS 5,Y,\n"
                 "選手,77848,CHI Yu Jen,,,MS 22,Y,\n"
                 "選手,,,戴資穎,,,N,已退休\n"
                 "組合,,A / B,,,MD 10,Y,\n", encoding="utf-8")
    rows = zh.load_player_table(f)
    assert rows == [{"player_id": "34810", "name_zh": "周天成", "name_en": "CHOU Tien Chen", "track": "Y"}]


def test_new_taiwan_sources_parse_real_pages():
    """notes 16:00 新來源（2026-10-01 真實列表頁）：每則都要有網址、標題、日期；非羽球被關鍵字濾掉。"""
    import re
    from pathlib import Path
    fx = Path(__file__).parent / "fixtures"
    for src, n in (("ettoday", 9), ("pts", 14), ("tsna", 8)):
        page = (fx / f"news_{src}_2026-10-01.html").read_text(encoding="utf-8")
        rows = news.parse(src, page, re.compile("."))
        assert len(rows) == n and all(r["published"] == "2026-10-01" and r["title"] and r["url"].startswith("https://") for r in rows)
        assert news.parse(src, page) == []                     # 這三頁當時沒有羽球新聞
    pts = (fx / "news_pts_2026-10-01.html").read_text(encoding="utf-8")
    assert [r["title"][:6] for r in news.parse("pts", pts, re.compile("網球"))] == ["搶十大戰上演"]


def test_news_run_log_and_zero_alert():
    """notes 06:20：每次每來源記解析／符合／新增；連續 24 小時整頁解析 0 則才算壞掉（至少 3 次紀錄）。"""
    import sqlite3
    con = sqlite3.connect(":memory:")
    con.executescript(news.RUN_TABLE)
    con.executemany("INSERT INTO news_run (run_at, source, parsed, matched, added) VALUES (datetime('now', ?), ?, ?, 0, 0)",
                    [("-1 hours", "pts", 0), ("-2 hours", "pts", 0), ("-3 hours", "pts", 0),
                     ("-1 hours", "cna", 19), ("-2 hours", "cna", 0), ("-3 hours", "cna", 0),
                     ("-1 hours", "tsna", 0)])
    assert news.zero_sources(con) == ["pts"]


def test_full_text_filter_rule():
    """notes 06:25 D：「羽球／羽毛球」≥ 1 次，或選手名合計 ≥ 2 次；綜合報導順帶一句不算。"""
    names = ["周天成", "戴資穎"]
    assert news.is_badminton("亞運戰況", "周天成今天出賽羽毛球男單，羽球館爆滿。", names)   # 07:25：全文 ≥ 2 次
    assert news.is_badminton("周天成返台", "周天成說還要拚奧運。", names)            # 選手名 2 次
    assert not news.is_badminton("中職戰報", "賽後球員提到偶像周天成。", names)       # 只順帶 1 次


def test_check_articles_fetches_once(tmp_path):
    import sqlite3

    class Resp:
        def __init__(self, text):
            self.text = text

    class Client:
        calls = []

        def get(self, url, **k):
            self.calls.append(url)
            return Resp("<p>周天成在羽球男單晉級，羽球館爆滿。</p>" if url.endswith("/1") else "<p>中職今天開打。</p>")

    con = sqlite3.connect(":memory:")
    con.executescript(news.RUN_TABLE)
    rows = [{"url": "https://x/1", "title": "亞運", "source": "cna", "published": "2026-10-02"},
            {"url": "https://x/2", "title": "中職", "source": "cna", "published": "2026-10-02"}]
    c = Client()
    passed, fetched = news.check_articles(con, c, "cna", rows, ["周天成"])
    assert [r["url"] for r in passed] == ["https://x/1"] and fetched == 2
    again, fetched2 = news.check_articles(con, c, "cna", rows, ["周天成"])
    assert again == [] and fetched2 == 0 and len(c.calls) == 2                       # 同一網址只抓一次
    assert con.execute("SELECT text FROM foreign_article").fetchone()[0].startswith("周天成")


def test_filter_rule_0725_tennis_counterexample():
    """07:25 第 2 點：標題有羽球、或全文羽球 ≥ 2 次、或選手名 ≥ 2 次。反例：10-02 誤收的網球新聞（全文只順帶一次「羽球」）。"""
    tennis_title = "亞運網球女雙闖4強保底銅牌 謝淑薇直言賽程不合理"
    tennis_body = "謝淑薇與梁恩碩在女雙八強獲勝。同一天，中華隊在羽球項目也有收穫。網球隊明天再戰。"
    assert not news.is_badminton(tennis_title, tennis_body, ["周天成"])
    assert news.is_badminton("羽球亞運 周天成晉級", "", [])                          # 標題有羽球
    assert news.is_badminton("亞運戰況", "羽球男單開打。羽毛球館爆滿。", [])           # 全文 2 次
    assert news.is_badminton("周天成返台", "周天成說還要拚奧運。", ["周天成"])         # 選手名 2 次


def test_two_char_names_do_not_count():
    """10-03 Raymond 1A：ETtoday 棒球新聞寫兩次「馬丁利」，「馬丁」（MARTHIN 譯名）不能讓它過關。"""
    from pathlib import Path
    from brief import foreign_names
    page = (Path(__file__).parent / "fixtures" / "article_pages" / "ettoday_3248227.html").read_text(encoding="utf-8")
    text = foreign_names.page_text(page, "ettoday")
    assert text.count("馬丁") >= 2
    assert not news.is_badminton("從9勝19敗到殺進季後賽 馬丁利仍傳卸任費城人代理總教練", text, ["馬丁", "周天成"])
    assert news.is_badminton("返台", "周天成說還要拚奧運，周天成週六飛芬蘭。", ["馬丁", "周天成"])      # 三個字照算
