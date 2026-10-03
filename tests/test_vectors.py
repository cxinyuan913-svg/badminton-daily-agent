"""交接單 007：切段、選手標記、RRF、增量／冪等、過濾。用假的 embedding（字元三元組雜湊），不載入真模型。"""
import hashlib
import math
import sqlite3

import pytest

pytest.importorskip("sqlite_vec")

from brief import vectors  # noqa: E402


def fake_embed(texts):
    """字元三元組雜湊到 1024 維再正規化：字面越像，內積越高（夠用來測排序與過濾）。"""
    out = []
    for t in texts:
        v = [0.0] * vectors.DIM
        s = t.lower()
        for i in range(len(s) - 2):
            v[int(hashlib.md5(s[i:i + 3].encode()).hexdigest(), 16) % vectors.DIM] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / n for x in v])
    return out


# 新聞標題取自 TSNA 回補的真實標題（2024-10～12），內文為測試用的短句
ARTICLES = [
    ("https://tsna/1", "tsna", "2024-12-04 10:00", "羽球》戴資穎親曝將動刀！　「把握可以自由活動的時間」",
     "戴資穎在社群上說明，今年賽季結束後將接受手術，術後需要休養。她表示會把握可以自由活動的時間陪伴家人。"),
    ("https://tsna/2", "tsna", "2024-10-13 21:00", "羽球》周天成擊敗印尼Christie　芬蘭公開賽封王",
     "周天成在芬蘭公開賽男單決賽擊敗印尼的 Jonatan CHRISTIE，拿下本季第二座冠軍。"),
    ("https://bwf/3", "bwf", "2024-12-03 08:00", "Axelsen withdraws from World Tour Finals",
     "Viktor Axelsen has withdrawn from the HSBC BWF World Tour Finals due to an injury. "
     "The Dane said he needed time to recover before the new season. " * 3),
]


def brief_db():
    con = sqlite3.connect(":memory:")
    con.executescript("""
        CREATE TABLE player (player_id INTEGER PRIMARY KEY, name_display TEXT, name_zh TEXT);
        CREATE TABLE pairing (pairing_id INTEGER PRIMARY KEY, player_a_id INTEGER, player_b_id INTEGER);
        CREATE TABLE ranking_snapshot (pairing_id INTEGER);
        CREATE TABLE foreign_name (player_id INTEGER, name_zh TEXT, status TEXT);
        CREATE TABLE nickname (nickname TEXT, player_ids TEXT, status TEXT);
        CREATE TABLE news_item (url TEXT PRIMARY KEY, source TEXT, published TEXT, title TEXT);
        CREATE TABLE foreign_article (url TEXT PRIMARY KEY, text TEXT);
    """)
    con.executemany("INSERT INTO player VALUES (?,?,?)", [(1, "TAI Tzu Ying", "戴資穎"), (2, "CHOU Tien Chen", "周天成"),
                                                         (3, "Viktor AXELSEN", None), (4, "Jonatan CHRISTIE", None),
                                                         (5, "LI Yu", None)])
    con.executemany("INSERT INTO pairing VALUES (?,?,NULL)", [(10, 3), (11, 4), (12, 5)])
    con.executemany("INSERT INTO ranking_snapshot VALUES (?)", [(10,), (11,), (12,)])
    con.execute("INSERT INTO foreign_name VALUES (4, '喬納坦', 'confirmed')")
    for url, source, published, title, text in ARTICLES:
        con.execute("INSERT INTO news_item VALUES (?,?,?,?)", (url, source, published, title))
        con.execute("INSERT INTO foreign_article VALUES (?,?)", (url, text))
    return con


def test_split_zh_and_en_sizes_and_overlap():
    zh = "".join(f"第{i}句話講的是羽球比賽的過程與結果。" for i in range(60))
    parts = vectors.split(zh)
    assert len(parts) > 1 and all(len(p) <= vectors.ZH_SIZE for p in parts)
    assert all(p.endswith("。") for p in parts[:-1])                  # 盡量在句尾切
    assert parts[0][-20:] in parts[1] or parts[1][:10] in parts[0]   # 有重疊
    en = "Axelsen won the match in straight games. " * 80
    eparts = vectors.split(en)
    assert vectors.is_english(en) and max(len(p) for p in eparts) > vectors.ZH_SIZE
    assert all(len(p) <= vectors.EN_SIZE for p in eparts)
    assert vectors.split("") == []


def test_name_index_tags_zh_en_and_skips_short_keys():
    names = vectors.NameIndex.build(brief_db())
    assert names.players("戴資穎宣布開刀") == {1}
    assert names.players("Viktor Axelsen withdrew") == {3}                 # 英文全名不分大小寫
    assert names.players("喬納坦 在決賽輸球") == {4}                       # 確認過的外國譯名
    assert 5 not in names.players("Li Yu said")                            # LI Yu 只有 4 個字母，不比對


def test_rrf_and_fts_query():
    s = vectors.rrf([[1, 2, 3], [3, 1]])
    assert max(s, key=s.get) == 1 and s[3] > s[2]
    assert vectors.fts_query("戴資穎 動刀 injury") == '"戴資穎" OR "injury"'   # 兩個字的詞交給向量
    assert vectors.fts_query("傷 刀") is None


def test_index_is_incremental_and_idempotent():
    con, vcon = brief_db(), vectors.connect(":memory:")
    first = vectors.index_new(con, vcon, embed=fake_embed)
    assert first["articles"] == 3 and first["chunks"] >= 3
    again = vectors.index_new(con, vcon, embed=fake_embed)
    assert again["articles"] == 0 and vectors.stats(vcon)["chunks"] == first["chunks"]
    rebuilt = vectors.index_new(con, vcon, embed=fake_embed, rebuild=True)
    assert rebuilt["chunks"] == first["chunks"] == vcon.execute("SELECT count(*) FROM chunks_vec").fetchone()[0]


def test_index_new_does_not_load_model_without_new_articles():
    con, vcon = brief_db(), vectors.connect(":memory:")
    vectors.index_new(con, vcon, embed=fake_embed)
    called = []
    vectors.index_new(con, vcon, embed=lambda t: called.append(t) or fake_embed(t))
    assert called == []


def test_search_filters_by_player_and_date():
    con, vcon = brief_db(), vectors.connect(":memory:")
    vectors.index_new(con, vcon, embed=fake_embed)
    hits = vectors.search(vcon, "戴資穎 開刀 手術", fake_embed, k=3)
    assert hits[0]["url"] == "https://tsna/1" and "bm25" in hits[0]["via"]
    only_chou = vectors.search(vcon, "手術 injury", fake_embed, k=3, player_ids=[2])
    assert [h["url"] for h in only_chou] == ["https://tsna/2"]
    dec = vectors.search(vcon, "羽球", fake_embed, k=5, since="2024-12-01")
    assert {h["url"] for h in dec} <= {"https://tsna/1", "https://bwf/3"}
    assert vectors.search(vcon, "羽球", fake_embed, player_ids=[999]) == []
    assert len({h["url"] for h in vectors.search(vcon, "Axelsen injury", fake_embed, k=3)}) == \
        len(vectors.search(vcon, "Axelsen injury", fake_embed, k=3))      # 同一篇最多一段


def test_news_for_story_lines_have_source_and_url():
    con, vcon = brief_db(), vectors.connect(":memory:")
    vectors.index_new(con, vcon, embed=fake_embed)
    lines = vectors.news_for_story(con, ["Viktor AXELSEN"], "2024-12-20", kind="retired", vcon=vcon, embed=fake_embed)
    assert lines and "https://bwf/3" in lines[0] and "2024-12-03" in lines[0]
    assert vectors.news_for_story(con, ["Viktor AXELSEN"], "2025-06-01", vcon=vcon, embed=fake_embed) == []   # 超過 30 天
    assert vectors.news_for_story(con, ["NOBODY"], "2024-12-20", vcon=vcon, embed=fake_embed) is None        # 找不到 ID → 退回關鍵字


def test_lock_blocks_second_runner_and_clears_stale(tmp_path):
    import os
    import time
    path = tmp_path / "v.lock"
    with vectors.lock(path):
        with pytest.raises(vectors.Locked):
            with vectors.lock(path):
                pass
    assert not path.exists()
    path.write_text("old")
    old = time.time() - vectors.LOCK_STALE_SEC - 10
    os.utime(path, (old, old))
    with vectors.lock(path):                 # 殘留的舊鎖會被清掉
        pass
