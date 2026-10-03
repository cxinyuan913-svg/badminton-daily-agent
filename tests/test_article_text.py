"""10-03 Raymond 2A：台灣媒體內文只取文章本文（fixture 是 2026-10-03 抓的真實頁面）。"""
from pathlib import Path

import pytest

from brief import foreign_names, news

PAGES = Path(__file__).parent / "fixtures" / "article_pages"


def text(name):
    return foreign_names.page_text((PAGES / f"{name}.html").read_text(encoding="utf-8"), name.split("_")[0])


@pytest.mark.parametrize("name,first,last", [
    ("cna_202609300328", "（中央社記者蘇志畬新北30日電）台灣羽球一哥周天成", "（編輯：張銘坤）1150930"),
    ("ettoday_3248227", "▲馬丁利（Don Mattingly）", "繼續留在球隊體系內。"),
    ("nownews_6879473", "台灣羽球一哥周天成，在結束愛知名古屋亞運征程後", "一步一步來。」"),
    ("pts_829775", "今（2026）年是台電創立80周年", "現場喜氣洋洋。"),
])
def test_body_only(name, first, last):
    t = text(name).strip()
    assert t.startswith(first) and t.endswith(last)
    for noise in ("隱私", "立刻加入", "我是廣告", "window.CONFIG", "相關新聞", "</div"):
        assert noise not in t


def test_tennis_article_no_longer_counts_as_badminton():
    """整頁的選單裡有「羽球」連結，曾讓這篇網球新聞過關；只看本文時「羽球」只有 1 次。"""
    t = text("cna_202610010253")
    assert t.count("羽球") == 1
    assert not news.is_badminton("亞運網球女雙闖4強保底銅牌 謝淑薇直言賽程不合理", t, ["周天成"])


def test_unknown_layout_falls_back_to_all_paragraphs():
    page = "<html><p>選單</p><div class='x'><p>羽球正文</p></div></html>"
    assert foreign_names.page_text(page, "cna") == "選單\n羽球正文"         # 找不到容器 → 退回整頁 <p>
    assert foreign_names.page_text(page) == "選單\n羽球正文"
