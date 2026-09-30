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
    assert digest.is_upset(None, 30)            # 無排名勝有排名
    assert digest.is_upset(15, 5)               # 差 10 名
    assert not digest.is_upset(14, 5)
    assert digest.is_upset(200, 100)            # 名次至少是兩倍
    assert not digest.is_upset(150, 100)
    assert not digest.is_upset(3, 40)           # 高排名勝低排名
    assert not digest.is_upset(40, None)


def test_digest_lists_only_new_matches_and_scores_winner_first():
    con = db()
    text, matches, ties = digest.build(con, D("2026-10-01"))
    assert len(matches) == 6 and ties == []     # 湯尤盃在 7 天以外
    assert "MAXX North Harbour International 2026" in text
    for m in matches:                            # 勝方比分在前：每局勝方分數較高的局數要過半
        games = [tuple(map(int, g.split("-"))) for g in m["score"].split()]
        if m["status"] == "Normal":
            assert sum(a > b for a, b in games) > len(games) / 2

    digest.mark_sent(con, D("2026-10-01"), matches, ties)
    text2, matches2, _ = digest.build(con, D("2026-10-01"))
    assert matches2 == [] and "沒有新的賽果" in text2
    digest.mark_sent(con, D("2026-10-01"), matches, ties)   # 重複標記不出錯
    assert con.execute("SELECT COUNT(*) FROM digest_item").fetchone()[0] == 6


def test_upset_is_highlighted_with_ranks():
    con = db()
    _, matches, _ = digest.build(con, D("2026-10-01"))
    m = matches[0]
    loser_pairing = con.execute("SELECT CASE winner_side WHEN 1 THEN side2_id ELSE side1_id END FROM match "
                                "WHERE match_id=?", (m["match_id"],)).fetchone()[0]
    con.execute("INSERT INTO ranking_snapshot (week_date, event, pairing_id, rank) VALUES ('2026-09-29', ?, ?, 12)",
                (m["event"], loser_pairing))
    text, matches, _ = digest.build(con, D("2026-10-01"))
    hit = next(x for x in matches if x["match_id"] == m["match_id"])
    assert hit["upset"] and hit["loser_rank"] == 12 and hit["winner_rank"] is None
    assert "⚡爆冷" in text and "#12" in text


def test_team_tie_line():
    con = db()
    text, _, ties = digest.build(con, D("2026-05-02"))
    assert [(t["team1"], t["score1"], t["score2"], t["team2"]) for t in ties] == [("CHN", 3, 0, "JPN")]
    assert "Uber Cup SF：CHN 3–0 JPN（CHN 勝）" in text


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
