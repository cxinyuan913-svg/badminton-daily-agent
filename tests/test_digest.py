"""每日摘要與 Discord 推送測試：用 North Harbour 2026-09-30 與湯尤盃四強的真實回應。"""
import datetime as dt
import json
from pathlib import Path

import pytest

from brief import crawler, digest, discord

FIXDIR = Path(__file__).parent / "fixtures"
D = dt.date.fromisoformat


def load(name):
    return json.loads((FIXDIR / name).read_text(encoding="utf-8"))


def db():
    con = crawler.connect(":memory:")
    crawler.upsert_tournament(con, {"tournament_id": 5766, "code": "1B95960C-1C1B-41E2-B7CF-120E8CA38CE3",
                                    "name": "MAXX North Harbour International 2026", "level": "IC",
                                    "start_date": "2026-09-30", "end_date": "2026-10-04", "source_url": ""})
    crawler.store_day(con, 5766, load("north_harbour_2026-09-30_sample.json"))
    crawler.upsert_tournament(con, {"tournament_id": 5600, "code": "X", "name": "BWF Thomas & Uber Cup Finals 2026",
                                    "level": "G1_TEAM", "start_date": "2026-04-24", "end_date": "2026-05-03",
                                    "source_url": ""})
    crawler.store_day(con, 5600, load("uber_cup_2026_sf_chn_jpn.json"))
    con.commit()
    return con


def test_upset_rule():
    """2026-09-30 Raymond：種子被 100 名以外擊敗 = 大爆冷、50 名以外 = 爆冷，種子自己的排名要在門檻以內。"""
    assert digest.upset_level(120, 8, "1") == "大爆冷"
    assert digest.upset_level(None, 30, "2") == "大爆冷"      # 無排名視為門檻以外
    assert digest.upset_level(60, 8, "3") == "爆冷"
    assert digest.upset_level(120, 70, "5") == "大爆冷"      # 種子 #70 在前 100 內，輸給 100 名外
    assert digest.upset_level(60, 70, "5") is None            # 種子 #70 不在前 50，輸給 #60 不算
    assert digest.upset_level(45, 8, "1") is None             # 勝方在前 50 內
    assert digest.upset_level(300, 12, None) is None          # 敗方不是種子
    assert digest.upset_level(300, None, "4") is None         # 種子沒有排名，無法判斷


def test_digest_lists_only_new_matches_and_scores_winner_first():
    con = db()
    text, matches, ties, _ = digest.build(con, D("2026-10-01"))
    assert len(matches) == 6 and ties == []     # 湯尤盃在 7 天以外
    assert "MAXX North Harbour International 2026" in text
    for m in matches:                            # 勝方比分在前：每局勝方分數較高的局數要過半
        games = [tuple(map(int, g.split("-"))) for g in m["score"].split()]
        if m["status"] == "Normal":
            assert sum(a > b for a, b in games) > len(games) / 2

    digest.mark_sent(con, D("2026-10-01"), matches, ties)
    text2, matches2, _, _ = digest.build(con, D("2026-10-01"))
    assert matches2 == [] and "沒有新的賽果" in text2
    digest.mark_sent(con, D("2026-10-01"), matches, ties)   # 重複標記不出錯
    assert con.execute("SELECT COUNT(*) FROM digest_item").fetchone()[0] == 6


def test_upset_is_highlighted_with_ranks():
    con = db()
    _, matches, _, _ = digest.build(con, D("2026-10-01"))
    m = matches[0]
    loser_pairing = con.execute("SELECT CASE winner_side WHEN 1 THEN side2_id ELSE side1_id END FROM match "
                                "WHERE match_id=?", (m["match_id"],)).fetchone()[0]
    con.execute("INSERT INTO ranking_snapshot (week_date, event, pairing_id, rank) VALUES ('2026-09-29', ?, ?, 12)",
                (m["event"], loser_pairing))
    text, matches, _, _ = digest.build(con, D("2026-10-01"))
    assert next(x for x in matches if x["match_id"] == m["match_id"])["upset"] is None   # 還不是種子
    con.execute("UPDATE match SET side1_seed='1', side2_seed='1' WHERE match_id=?", (m["match_id"],))
    text, matches, _, _ = digest.build(con, D("2026-10-01"))
    hit = next(x for x in matches if x["match_id"] == m["match_id"])
    assert hit["upset"] == "大爆冷" and hit["loser_rank"] == 12 and hit["winner_rank"] is None
    assert "💥大爆冷" in text and "#12" in text


def test_team_tie_line():
    con = db()
    text, _, ties, _ = digest.build(con, D("2026-05-02"))
    assert [(t["team1"], t["score1"], t["score2"], t["team2"]) for t in ties] == [("CHN", 3, 0, "JPN")]
    assert "尤伯盃 四強：中國 3–0 日本（中國勝）" in text
    assert "__**2026 湯尤盃**__（Grade 1 團體）" in text


def test_split_message_respects_limit():
    text = "\n".join(f"第 {i} 行 " + "x" * 50 for i in range(200))
    chunks = discord.split_message(text, limit=500)
    assert all(len(c) <= 500 for c in chunks)
    assert "\n".join(chunks) == text
    assert discord.split_message("y" * 1200, limit=500) == ["y" * 500, "y" * 500, "y" * 200]


class FakeSession:
    def __init__(self, codes):
        self.codes, self.posts = list(codes), []

    def post(self, url, json, timeout):
        self.posts.append(json["content"])
        code = self.codes.pop(0)

        class R:
            status_code = code

            def json(self):
                return {"retry_after": 0}

            def raise_for_status(self):
                if code >= 400:
                    raise RuntimeError(code)
        return R()


def test_send_retries_rate_limit_and_raises_on_failure(monkeypatch):
    monkeypatch.setattr(discord.time, "sleep", lambda s: None)
    s = FakeSession([429, 204])
    assert discord.send("https://example/webhook", "hello", session=s) == 1
    assert s.posts == ["hello", "hello"]
    with pytest.raises(RuntimeError):
        discord.send("https://example/webhook", "hello", session=FakeSession([500]))


def test_webhook_reads_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_DAILY", raising=False)
    env = tmp_path / ".env"
    env.write_text("DISCORD_WEBHOOK_DAILY=\nDISCORD_WEBHOOK_ALERTS=https://a\n", encoding="utf-8")
    assert discord.webhook("DISCORD_WEBHOOK_ALERTS", env) == "https://a"
    with pytest.raises(RuntimeError):
        discord.webhook("DISCORD_WEBHOOK_DAILY", env)          # 空值視為未設定


def test_news_section_recent_only_and_marked():
    from brief import news
    con = db()
    news.store(con, news.parse("bwf", (FIXDIR / "bwf_news_2026-09-30.html").read_text(encoding="utf-8")))
    text, _, _, items = digest.build(con, D("2026-09-30"))
    assert [n["published"] for n in items] == ["2026-09-30"]            # 9/28–9/30 只有一則
    assert "World Juniors: Africa Beckons" in text
    assert "<https://bwfbadminton.com/news-single/2026/09/30/poised-to-make-deep-inroads/>" in text
    digest.mark_sent(con, D("2026-09-30"), [], [], items)
    assert digest.build(con, D("2026-09-30"))[3] == []


def test_include_sent_for_retesting():
    con = db()
    _, matches, _, _ = digest.build(con, D("2026-10-01"))
    digest.mark_sent(con, D("2026-10-01"), matches, [])
    assert digest.build(con, D("2026-10-01"))[1] == []
    assert len(digest.build(con, D("2026-10-01"), include_sent=True)[1]) == len(matches)


def test_walkover_is_not_upset_and_has_no_empty_score():
    con = db()
    _, matches, _, _ = digest.build(con, D("2026-10-01"))
    m = matches[0]
    loser = con.execute("SELECT CASE winner_side WHEN 1 THEN side2_id ELSE side1_id END FROM match WHERE match_id=?",
                        (m["match_id"],)).fetchone()[0]
    con.execute("INSERT INTO ranking_snapshot (week_date, event, pairing_id, rank) VALUES ('2026-09-29', ?, ?, 12)",
                (m["event"], loser))
    con.execute("UPDATE match SET score_status='Walkover', side1_seed='1', side2_seed='1' WHERE match_id=?",
                (m["match_id"],))
    con.execute("DELETE FROM game WHERE match_id=?", (m["match_id"],))
    _, matches, _, _ = digest.build(con, D("2026-10-01"))
    hit = next(x for x in matches if x["match_id"] == m["match_id"])
    assert not hit["upset"]
    assert digest._line(hit).endswith("）（不戰而勝）")
