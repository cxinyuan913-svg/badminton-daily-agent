"""羽球日報 Agent：每日文字摘要（P1，先不用 LLM）

從資料庫挑出「還沒推送過」的比賽，整理成 Discord 訊息。內容全部來自資料庫，
每個比分、名字、排名都查得到；LLM 改寫是之後的事，不影響這裡的事實。

每站賽事：
  - 八強以後：全部列出
  - 更早的輪次：只列爆冷與台灣選手，其餘只給場數
  - 團體賽：列各場對戰的國家比分

爆冷的門檻（UPSET_*）是暫定值，待 Raymond 確認。

用法：
  python -m brief.digest --db data/brief.db                # 印出摘要，不推送
  python -m brief.digest --db data/brief.db --send         # 推送到 DISCORD_WEBHOOK_DAILY，並標記已推送
"""
from __future__ import annotations

import argparse
import datetime as dt
from collections import defaultdict

from brief import discord
from brief.crawler import connect
from brief.news import NEWS_TABLE
from brief.rankings import rank_on

LATE_ROUNDS = {"QF", "SF", "Final", "F"}
ROUND_ORDER = ["Q1", "Q2", "Q3", "R128", "R64", "R32", "R16", "R1", "R2", "R3", "QF", "SF", "Final", "F"]
EVENT_ORDER = ["MS", "WS", "MD", "WD", "XD"]
HOME_COUNTRY = "TPE"
NEWS_DAYS = 2              # 新聞只推最近兩天發布的
LOOKBACK_DAYS = 7         # 只看最近幾天的比賽，避免第一次推送時把十年份全推出去
# 暫定：敗方有排名，且勝方沒排名或名次至少差 max(UPSET_MIN_GAP, 敗方名次)，也就是勝方名次至少是敗方的兩倍
UPSET_MIN_GAP = 10

DIGEST_TABLE = """
CREATE TABLE IF NOT EXISTS digest_item (
    match_id        INTEGER PRIMARY KEY,
    digest_date     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS digest_tie (
    team_tie_id     INTEGER PRIMARY KEY,
    digest_date     TEXT NOT NULL
);
"""


def is_upset(winner_rank: int | None, loser_rank: int | None) -> bool:
    if loser_rank is None:
        return False
    if winner_rank is None:
        return True
    return winner_rank - loser_rank >= max(UPSET_MIN_GAP, loser_rank)


def _side(con, pairing_id: int) -> dict:
    rows = con.execute(
        """SELECT pl.player_id, COALESCE(pl.name_display, pl.slug, pl.player_id), pl.country_code
           FROM pairing p JOIN player pl ON pl.player_id IN (p.player_a_id, p.player_b_id)
           WHERE p.pairing_id=? ORDER BY pl.player_id""", (pairing_id,)).fetchall()
    countries = sorted({r[2] for r in rows if r[2]})
    return {"name": " / ".join(str(r[1]) for r in rows), "country": "/".join(countries),
            "home": HOME_COUNTRY in countries}


def pending_matches(con, today: dt.date) -> list[dict]:
    since = (today - dt.timedelta(days=LOOKBACK_DAYS)).isoformat()
    rows = con.execute(
        """SELECT m.match_id, m.tournament_id, t.name, t.level, m.event, m.round, m.match_date,
                  m.side1_id, m.side2_id, m.winner_side, m.score_status, m.team_tie_id
           FROM match m JOIN tournament t USING (tournament_id)
           LEFT JOIN digest_item d USING (match_id)
           WHERE d.match_id IS NULL AND m.match_date >= ? AND m.match_date <= ?
           ORDER BY m.match_date, m.match_id""", (since, today.isoformat())).fetchall()
    out = []
    for (mid, tid, tname, level, event, rnd, mdate, s1, s2, win, status, tie) in rows:
        w, l = (s1, s2) if win == 1 else (s2, s1)
        games = con.execute("SELECT side1_points, side2_points FROM game WHERE match_id=? ORDER BY game_no",
                            (mid,)).fetchall()
        score = " ".join(f"{a}-{b}" if win == 1 else f"{b}-{a}" for a, b in games)   # 勝方在前
        wr, lr = rank_on(con, w, event, mdate), rank_on(con, l, event, mdate)
        out.append({
            "match_id": mid, "tournament_id": tid, "tournament": tname, "level": level, "event": event,
            "round": rnd, "date": mdate, "winner": _side(con, w), "loser": _side(con, l),
            "winner_rank": wr, "loser_rank": lr, "score": score, "status": status,
            "team_tie_id": tie, "upset": is_upset(wr, lr),
        })
    return out


def pending_ties(con, today: dt.date) -> list[dict]:
    since = (today - dt.timedelta(days=LOOKBACK_DAYS)).isoformat()
    rows = con.execute(
        """SELECT tt.team_tie_id, tt.tournament_id, tt.competition, tt.round, tt.match_date,
                  tt.team1_country, tt.team2_country, tt.team1_score, tt.team2_score, tt.winner_side
           FROM team_tie tt LEFT JOIN digest_tie d USING (team_tie_id)
           WHERE d.team_tie_id IS NULL AND tt.winner_side IS NOT NULL
             AND tt.match_date >= ? AND tt.match_date <= ?
           ORDER BY tt.match_date, tt.team_tie_id""", (since, today.isoformat())).fetchall()
    keys = ["team_tie_id", "tournament_id", "competition", "round", "date", "team1", "team2",
            "score1", "score2", "winner_side"]
    return [dict(zip(keys, r)) for r in rows]


def _rank(r):
    return f"#{r}" if r else "無排名"


def _line(m: dict) -> str:
    w, l = m["winner"], m["loser"]
    tags = []
    if m["upset"]:
        tags.append("⚡爆冷")
    if w["home"] or l["home"]:
        tags.append("🇹🇼")
    status = "" if m["status"] in (None, "Normal") else f"（{m['status']}）"
    return (f"- {m['event']} {m['round']}：**{w['name']}**（{w['country']}，{_rank(m['winner_rank'])}）勝 "
            f"{l['name']}（{l['country']}，{_rank(m['loser_rank'])}） {m['score']}{status}"
            + (f"  {' '.join(tags)}" if tags else ""))


def _sort_key(m):
    ev = EVENT_ORDER.index(m["event"]) if m["event"] in EVENT_ORDER else 9
    rnd = ROUND_ORDER.index(m["round"]) if m["round"] in ROUND_ORDER else -1
    return (-rnd, ev, m["match_id"])


def render(today: dt.date, matches: list[dict], ties: list[dict], news: list[dict] = ()) -> str:
    lines = [f"**羽球日報 {today.isoformat()}**"]
    if not matches and not ties:
        lines.append("今天沒有新的賽果。")
    lines += _render_results(matches, ties)
    if news:
        lines += ["", "__**新聞**__（只當資訊來源，引用要改寫並附出處）"]
        for n in news:
            lines.append(f"- [{n['source']}] {n['title']}（{(n['published'] or '')[:10]}） <{n['url']}>")
    return "\n".join(lines)


def _render_results(matches: list[dict], ties: list[dict]) -> list[str]:
    lines: list[str] = []
    by_t: dict[int, list[dict]] = defaultdict(list)
    names: dict[int, tuple[str, str | None]] = {}
    for m in matches:
        by_t[m["tournament_id"]].append(m)
        names[m["tournament_id"]] = (m["tournament"], m["level"])
    ties_by_t: dict[int, list[dict]] = defaultdict(list)
    for t in ties:
        ties_by_t[t["tournament_id"]].append(t)

    for tid in sorted(set(by_t) | set(ties_by_t)):
        name, level = names.get(tid, (f"賽事 {tid}", None))
        ms = by_t.get(tid, [])
        dates = sorted({m["date"] for m in ms} | {t["date"] for t in ties_by_t.get(tid, [])})
        lines.append("")
        lines.append(f"__**{name}**__" + (f"（{level}）" if level else "") + f"　{dates[0]}" +
                     (f" → {dates[-1]}" if dates[-1] != dates[0] else ""))
        for t in ties_by_t.get(tid, []):
            w = t["team1"] if t["winner_side"] == 1 else t["team2"]
            lines.append(f"- {t['competition']} {t['round']}：{t['team1']} {t['score1']}–{t['score2']} {t['team2']}"
                         f"（{w} 勝）")
        individual = [m for m in ms if m["team_tie_id"] is None]
        late = [m for m in individual if m["round"] in LATE_ROUNDS]
        early = [m for m in individual if m["round"] not in LATE_ROUNDS]
        highlight = [m for m in ms if m not in late and (m["upset"] or m["winner"]["home"] or m["loser"]["home"])]
        for m in sorted(late, key=_sort_key):
            lines.append(_line(m))
        for m in sorted(highlight, key=_sort_key):
            lines.append(_line(m))
        if early:
            rest = len(early) - len([m for m in highlight if m["team_tie_id"] is None])
            counts = defaultdict(int)
            for m in early:
                counts[m["round"]] += 1
            summary = "、".join(f"{r} {counts[r]} 場" for r in sorted(counts, key=lambda r: ROUND_ORDER.index(r)
                                                                    if r in ROUND_ORDER else -1))
            lines.append(f"- 其他：{summary}" + (f"（未列出 {rest} 場）" if rest else ""))
    return lines


def pending_news(con, today: dt.date) -> list[dict]:
    """最近 NEWS_DAYS 天、還沒推送過的新聞。"""
    since = (today - dt.timedelta(days=NEWS_DAYS)).isoformat()
    rows = con.execute(
        """SELECT n.url, n.source, n.title, n.published FROM news_item n
           LEFT JOIN digest_news d USING (url)
           WHERE d.url IS NULL AND substr(n.published, 1, 10) BETWEEN ? AND ?
           ORDER BY n.published DESC, n.url""", (since, today.isoformat())).fetchall()
    return [dict(zip(("url", "source", "title", "published"), r)) for r in rows]


def build(con, today: dt.date) -> tuple[str, list[dict], list[dict], list[dict]]:
    con.executescript(DIGEST_TABLE + NEWS_TABLE)
    matches, ties, news = pending_matches(con, today), pending_ties(con, today), pending_news(con, today)
    return render(today, matches, ties, news), matches, ties, news


def mark_sent(con, today: dt.date, matches: list[dict], ties: list[dict], news: list[dict] = ()) -> None:
    con.executemany("INSERT OR IGNORE INTO digest_news (url, digest_date) VALUES (?, ?)",
                    [(n["url"], today.isoformat()) for n in news])
    con.executemany("INSERT OR IGNORE INTO digest_item (match_id, digest_date) VALUES (?, ?)",
                    [(m["match_id"], today.isoformat()) for m in matches])
    con.executemany("INSERT OR IGNORE INTO digest_tie (team_tie_id, digest_date) VALUES (?, ?)",
                    [(t["team_tie_id"], today.isoformat()) for t in ties])
    con.commit()


def main():
    from brief.daily import today_taipei
    ap = argparse.ArgumentParser(description="每日文字摘要")
    ap.add_argument("--db", default="brief.db")
    ap.add_argument("--date", help="YYYY-MM-DD，預設台北今天")
    ap.add_argument("--send", action="store_true", help="推送到 Discord 並標記已推送")
    a = ap.parse_args()
    today = dt.date.fromisoformat(a.date) if a.date else today_taipei()
    con = connect(a.db)
    text, matches, ties, news = build(con, today)
    if not a.send:
        print(text)
        return
    discord.send(discord.webhook("DISCORD_WEBHOOK_DAILY"), text)
    mark_sent(con, today, matches, ties, news)
    print(f"已推送：{len(matches)} 場、{len(ties)} 場團體對戰、{len(news)} 則新聞")


if __name__ == "__main__":
    main()
