"""中華羽協甲組名單比對（notes 10-02 05:55 第 5 項）：名單解析的怪格式、拼音比對、只有距離 0 才自動採用。"""
from brief import ctba


def test_roster_parsing_quirks():
    text = ("陳 \xa0\xa0 康 楊智勛 袁尚德\n"
            "王義傑 李明倫 林家弘 范姜明\n"
            "盛 張豐進\n"
            "侯賢哲(殁) 陳玉珍(大) 林　涓\n"
            "洪桑笛 (改名成 李忻瑋)\n"
            "114年第一、二次排名賽晉升名單\n")
    names = ctba.roster_names(text)
    for n in ("陳康", "楊智勛", "范姜明盛", "張豐進", "侯賢哲", "陳玉珍", "林涓", "洪桑笛", "李忻瑋"):
        assert n in names, n
    assert not any("晉升" in n or "殁" in n for n in names)


def test_pinyin_matching():
    assert ctba.split_bwf("Yu Hsiang CHOU") == ("chou", "yuhsiang")
    assert ctba.score("LIANG Ting Yu", "梁庭瑜") == 0             # notes 05:55 的例子
    assert ctba.score("LIANG Ting Yu", "林庭瑜") is None          # 姓對不上
    hits = ctba.match("LIANG Ting Yu", ["梁庭瑜", "梁婷宇", "王庭瑜"])
    assert [h for h in hits if h[0] == 0] == [(0, "梁婷宇"), (0, "梁庭瑜")]   # 同音字兩個都 0 → run() 會列待確認、不自動採用
