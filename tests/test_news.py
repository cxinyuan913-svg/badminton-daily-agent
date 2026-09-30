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
