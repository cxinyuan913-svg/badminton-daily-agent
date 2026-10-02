"""新聞兩年回補（notes 07:25 第 1 點）：解析 BWF 分頁接口與 TSNA 文章頁（不連網）。"""
from brief import news_backfill as nb


def test_bwf_item_builds_url_and_text():
    it = nb.bwf_item({"post_date": "2026-10-02T09:02:48.000000Z", "post_name": "take-off-time-for-senegal-badminton",
                      "post_title": "Take-Off Time for Senegal Badminton", "post_content": "First line.\r\n\r\n<strong>Coach</strong> says hi."})
    assert it["url"] == "https://bwfbadminton.com/news-single/2026/10/02/take-off-time-for-senegal-badminton/"
    assert it["published"] == "2026-10-02" and it["text"] == "First line.\nCoach says hi."


def test_tsna_parse():
    page = ('<meta property="og:title" content="亞運直擊》葉宏蔚／詹又蓁收混雙銅牌"/>'
            '<meta property="article:published_time" content="2026-09-28T10:59:45+08:00">'
            '<div id="centercontainer" class="userBlk"> 葉宏蔚／詹又蓁今天在羽球混雙4強戰<br><br>以銅牌結束亞運。</div>')
    title, date, text = nb.tsna_parse(page)
    assert title.startswith("亞運直擊") and date == "2026-09-28" and text == "葉宏蔚／詹又蓁今天在羽球混雙4強戰\n以銅牌結束亞運。"
