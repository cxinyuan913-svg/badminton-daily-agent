"""羽球日報 Agent：每日文字摘要（P1，先不用 LLM）

從資料庫挑出「還沒推送過」的比賽，整理成 Discord 訊息。內容全部來自資料庫，
每個比分、名字、排名都查得到；LLM 改寫是之後的事，不影響這裡的事實。

每站賽事：
  - 八強以後：全部列出
  - 更早的輪次：只列爆冷與台灣選手，其餘只給場數
  - 團體賽：列各場對戰的國家比分

爆冷規則（2026-09-30 Raymond 決議）：敗方必須是本站種子；種子序號依當次報名排出，不等於世界排名，
所以兩邊都用比賽當週的世界排名判斷，見 upset_level()。

用法：
  python -m brief.digest --db data/brief.db                # 印出摘要，不推送
  python -m brief.digest --db data/brief.db --send         # 推送到 DISCORD_WEBHOOK_DAILY，並標記已推送
"""
from __future__ import annotations

import argparse
import datetime as dt
from collections import defaultdict

from brief import discord, grade3, zh
from brief.crawler import connect
from brief.news import NEWS_TABLE
from brief.rankings import rank_lookup

LATE_ROUNDS = {"QF", "SF", "Final", "F"}
ROUND_ORDER = ["Qual. R64", "Qual. R32", "Qual. R16", "Qual. QF", "Q1", "Q2", "Q3", "R128", "R64", "R32", "R16", "R1", "R2", "R3", "QF", "SF", "Final", "F"]
EVENT_ORDER = ["MS", "WS", "MD", "WD", "XD"]
HOME_COUNTRY = "TPE"
NEWS_SOURCE = {"bwf": "BWF", "bwfworldtour": "BWF 世界巡迴賽", "cna": "中央社", "nownews": "NOWnews"}
NEWS_DAYS = 2              # 新聞只推最近兩天發布的
LOOKBACK_DAYS = 7         # 只看最近幾天的比賽，避免第一次推送時把十年份全推出去
# (門檻, 標籤)：種子的世界排名在門檻以內，輸給門檻以外（或無排名）的選手。由嚴到寬比對
UPSET_LEVELS = [(100, "大爆冷"), (50, "爆冷")]

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


def upset_level(winner_rank: int | None, loser_rank: int | None, loser_seed: str | None) -> str | None:
    """種子被世界排名 100 名以外擊敗 = 大爆冷、50 名以外 = 爆冷；前提是種子自己的排名在該門檻以內。
    無排名的勝方視為在任何門檻以外；種子沒有排名時無法判斷，不算。"""
    if not loser_seed or loser_rank is None:
        return None
    for limit, label in UPSET_LEVELS:
        if loser_rank <= limit and (winner_rank is None or winner_rank > limit):
            return label
    return None


def _side(con, pairing_id: int) -> dict:
    rows = con.execute(
        """SELECT pl.player_id, COALESCE(pl.name_display, pl.slug, pl.player_id), pl.country_code, pl.name_zh
           FROM pairing p JOIN player pl ON pl.player_id IN (p.player_a_id, p.player_b_id)
           WHERE p.pairing_id=? ORDER BY pl.player_id""", (pairing_id,)).fetchall()
    countries = sorted({r[2] for r in rows if r[2]})
    # 2026-09-30：單打「周天成（CHOU Tien Chen）」；雙打兩人都有中文名時寫「王齊麟／李哲輝」，否則逐人寫
    if len(rows) == 2 and all(r[3] for r in rows):
        name = "／".join(r[3] for r in rows)
    else:
        name = " / ".join(zh.player(str(r[1]), r[3]) for r in rows)
    return {"pairing_id": pairing_id, "name": name,
            "name_en": " / ".join(str(r[1]) for r in rows), "country": "/".join(countries),
            "home": HOME_COUNTRY in countries,
            "players": [(r[0], zh.player(str(r[1]), r[3]), r[2]) for r in rows]}


MATCH_COLUMNS = """m.match_id, m.tournament_id, t.name, t.level, m.event, m.round, m.match_date,
                  m.side1_id, m.side2_id, m.winner_side, m.score_status, m.team_tie_id,
                  m.side1_seed, m.side2_seed"""


def pending_matches(con, today: dt.date, include_sent: bool = False) -> list[dict]:
    since = (today - dt.timedelta(days=LOOKBACK_DAYS)).isoformat()
    rows = con.execute(
        f"""SELECT {MATCH_COLUMNS}
           FROM match m JOIN tournament t USING (tournament_id)
           LEFT JOIN digest_item d USING (match_id)
           WHERE (d.match_id IS NULL OR ?) AND m.match_date >= ? AND m.match_date <= ?
           ORDER BY m.match_date, m.match_id""", (include_sent, since, today.isoformat())).fetchall()
    return _to_dicts(con, rows)


def stage_matches(con, tournament_id: int, match_ids: list[int]) -> list[dict]:
    """指定場次（brief.watch 用：一站一天的全部場次）。"""
    if not match_ids:
        return []
    rows = con.execute(
        f"""SELECT {MATCH_COLUMNS} FROM match m JOIN tournament t USING (tournament_id)
            WHERE m.tournament_id=? AND m.match_id IN ({",".join("?" * len(match_ids))})
            ORDER BY m.match_id""", (tournament_id, *match_ids)).fetchall()
    return _to_dicts(con, rows)


def _to_dicts(con, rows) -> list[dict]:
    out = []
    for (mid, tid, tname, level, event, rnd, mdate, s1, s2, win, status, tie, seed1, seed2) in rows:
        w, l = (s1, s2) if win == 1 else (s2, s1)
        loser_seed = seed2 if win == 1 else seed1
        games = con.execute("SELECT side1_points, side2_points FROM game WHERE match_id=? ORDER BY game_no",
                            (mid,)).fetchall()
        score = " ".join(f"{a}-{b}" if win == 1 else f"{b}-{a}" for a, b in games)   # 勝方在前
        (wr, wsrc), (lr, lsrc) = rank_lookup(con, w, event, mdate), rank_lookup(con, l, event, mdate)
        out.append({
            "match_id": mid, "tournament_id": tid, "tournament": tname, "level": level, "event": event,
            "round": rnd, "date": mdate, "winner": _side(con, w), "loser": _side(con, l),
            "winner_rank": wr, "loser_rank": lr, "winner_rank_src": wsrc, "loser_rank_src": lsrc, "score": score, "status": status,
            "team_tie_id": tie, "loser_seed": loser_seed,
            "upset": None if status == "Walkover" else upset_level(wr, lr, loser_seed),   # 不戰而勝沒有真的比賽
        })
    return out


def pending_ties(con, today: dt.date, include_sent: bool = False) -> list[dict]:
    since = (today - dt.timedelta(days=LOOKBACK_DAYS)).isoformat()
    rows = con.execute(
        """SELECT tt.team_tie_id, tt.tournament_id, tt.competition, tt.round, tt.match_date,
                  tt.team1_country, tt.team2_country, tt.team1_score, tt.team2_score, tt.winner_side
           FROM team_tie tt LEFT JOIN digest_tie d USING (team_tie_id)
           WHERE (d.team_tie_id IS NULL OR ?) AND tt.winner_side IS NOT NULL
             AND tt.match_date >= ? AND tt.match_date <= ?
           ORDER BY tt.match_date, tt.team_tie_id""", (include_sent, since, today.isoformat())).fetchall()
    keys = ["team_tie_id", "tournament_id", "competition", "round", "date", "team1", "team2",
            "score1", "score2", "winner_side"]
    return [dict(zip(keys, r)) for r in rows]


def _rank(r, src=None):
    if not r:
        return "無排名"
    return f"#{r}（估算）" if src == "estimate" else f"#{r}"


def _line(m: dict) -> str:
    w, l = m["winner"], m["loser"]
    tags = []
    if m["upset"]:
        tags.append(("💥" if m["upset"] == "大爆冷" else "⚡") + m["upset"])
    if w["home"] or l["home"]:
        tags.append("🇹🇼")
    status = "" if m["status"] in (None, "Normal") else f"（{zh.status(m['status'])}）"
    return (f"- {zh.event(m['event'])} {zh.round_name(m['round'])}：**{w['name']}**（{zh.country(w['country'])}，"
            f"{_rank(m['winner_rank'], m.get('winner_rank_src'))}）勝 {l['name']}（{zh.country(l['country'])}，{_rank(m['loser_rank'], m.get('loser_rank_src'))}）"
            + (f" {m['score']}" if m["score"] else "") + status
            + (f"  {' '.join(tags)}" if tags else ""))


def _sort_key(m):
    ev = EVENT_ORDER.index(m["event"]) if m["event"] in EVENT_ORDER else 9
    rnd = ROUND_ORDER.index(m["round"]) if m["round"] in ROUND_ORDER else -1
    return (-rnd, ev, m["match_id"])


def render(today: dt.date, matches: list[dict], ties: list[dict], news: list[dict] = (),
           grade3_lines: list[str] = (), grade3_hidden: int = 0) -> str:
    lines = [f"**羽球日報 {today.isoformat()}**"]
    if not matches and not ties and not grade3_lines:
        lines.append("今天沒有新的賽果。")
    lines += _render_results(matches, ties)
    if grade3_lines:
        lines += ["", "__**IC／IS 精選**__"] + list(grade3_lines)
    if grade3_hidden:
        lines += ["", f"今天另有 IC／IS 共 {grade3_hidden} 場，已存入資料庫。"]
    if news:
        lines += ["", "__**新聞**__（只當資訊來源，引用要改寫並附出處）"]
        for n in news:
            lines.append(f"- 【{NEWS_SOURCE.get(n['source'], n['source'])}】{n['title']}"
                         f"（{(n['published'] or '')[:10]}） <{n['url']}>")
            if n.get("gist"):
                lines.append(f"  　重點：{n['gist']}")
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
        lines.append(f"__**{zh.tournament(name)}**__" + (f"（{zh.level(level)}）" if level else "") + f"　{dates[0]}" +
                     (f" → {dates[-1]}" if dates[-1] != dates[0] else ""))
        for t in ties_by_t.get(tid, []):
            w = t["team1"] if t["winner_side"] == 1 else t["team2"]
            lines.append(f"- {zh.competition(t['competition'])} {zh.round_name(t['round'])}："
                         f"{zh.country(t['team1'])} {t['score1']}–{t['score2']} {zh.country(t['team2'])}"
                         f"（{zh.country(w)}勝）")
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
            summary = "、".join(f"{zh.round_name(r)} {counts[r]} 場" for r in sorted(counts, key=lambda r: ROUND_ORDER.index(r)
                                                                    if r in ROUND_ORDER else -1))
            lines.append(f"- 其他：{summary}" + (f"（未列出 {rest} 場）" if rest else ""))
    return lines


def pending_news(con, today: dt.date, include_sent: bool = False) -> list[dict]:
    """最近 NEWS_DAYS 天、還沒推送過的新聞。"""
    since = (today - dt.timedelta(days=NEWS_DAYS)).isoformat()
    rows = con.execute(
        """SELECT n.url, n.source, n.title, n.published FROM news_item n
           LEFT JOIN digest_news d USING (url)
           WHERE (d.url IS NULL OR ?) AND substr(n.published, 1, 10) BETWEEN ? AND ?
           ORDER BY n.published DESC, n.url""", (include_sent, since, today.isoformat())).fetchall()
    return [dict(zip(("url", "source", "title", "published"), r)) for r in rows]


def build(con, today: dt.date, llm=None, errors: list | None = None, include_sent: bool = False) -> tuple[str, list[dict], list[dict], list[dict]]:
    """llm 有給就在最上方加「今日重點」；LLM 失敗時照常回傳事實摘要，錯誤放進 errors。"""
    con.executescript(DIGEST_TABLE + NEWS_TABLE)
    zh.apply_player_names(con)
    matches = pending_matches(con, today, include_sent)
    ties, news = pending_ties(con, today, include_sent), pending_news(con, today, include_sent)
    if llm is not None:
        from brief.llm import news_gist
        for n in news:
            if n["source"] in ("bwf", "bwfworldtour"):          # 台灣媒體標題本來就是中文
                try:
                    n["gist"] = news_gist(llm, n["title"])
                except Exception as e:  # noqa: BLE001
                    if errors is not None:
                        errors.append(f"llm news: {e!r}")
                    break
    pushed = [m for m in matches if m["level"] not in grade3.GRADE3 | grade3.NO_PUSH]
    g3_lines, g3_shown = grade3_section(con, [m for m in matches if m["level"] in grade3.GRADE3])
    g3_hidden = sum(1 for m in matches if m["level"] in grade3.GRADE3) - g3_shown
    from brief import nickname
    text = render(today, pushed, ties, news, g3_lines, g3_hidden) + "\n".join([""] + nickname.weekly_lines(con, today))
    if llm is not None and (pushed or ties or g3_lines):
        from brief.llm import highlight
        try:
            head, _, body = text.partition("\n")
            text = f"{head}\n{highlight(llm, body)}\n\n{body}"
        except Exception as e:  # noqa: BLE001
            if errors is not None:
                errors.append(f"llm: {e!r}")
    return text, matches, ties, news


MORNING_NEWS_DAYS = 1


def reminders(con, today: dt.date) -> list[str]:
    """晨報提醒：前一天 brief.watch 保險發送（還有沒打完的）或漏發的站。"""
    yesterday = (today - dt.timedelta(days=1)).isoformat()
    lines = []
    try:
        for name, day, n in con.execute(
                """SELECT t.name, s.local_date, s.unfinished FROM stage_digest s JOIN tournament t USING (tournament_id)
                   WHERE s.forced = 1 AND s.local_date >= ? ORDER BY s.local_date""",
                ((today - dt.timedelta(days=2)).isoformat(),)):
            lines.append(f"- 提醒：{zh.tournament(name)} 當地 {day} 保險發送，當時有 {n} 場未完成")
        for name, day in con.execute(
                """SELECT DISTINCT t.name, m.match_date FROM match m JOIN tournament t USING (tournament_id)
                   LEFT JOIN stage_digest s ON s.tournament_id = m.tournament_id AND s.local_date = m.match_date
                   WHERE m.match_date = ? AND s.local_date IS NULL
                     AND COALESCE(t.level, '') NOT IN ('IC', 'IS', 'FS')""", (yesterday,)):
            lines.append(f"- 提醒：{zh.tournament(name)} 當地 {day} 的賽果還沒發（brief.watch 可能沒執行）")
    except Exception:  # noqa: BLE001 — 還沒有 stage_digest（watch 沒跑過）
        pass
    return lines


def morning(con, today: dt.date, llm=None, errors: list | None = None) -> tuple[str | None, list[dict], list[dict]]:
    """06:00 晨報（notes 18:15）：最近 24 小時新聞＋ IC／IS 精選＋週一暱稱週報＋漏發提醒。
    Super 100 以上的賽果由 brief.watch 逐站發送，這裡不放。什麼都沒有就回傳 None（不發）。"""
    from brief import nickname
    con.executescript(DIGEST_TABLE + NEWS_TABLE)
    zh.apply_player_names(con)
    g3 = [m for m in pending_matches(con, today) if m["level"] in grade3.GRADE3]
    since = (today - dt.timedelta(days=MORNING_NEWS_DAYS)).isoformat()
    news = [n for n in pending_news(con, today) if (n["published"] or "")[:10] >= since]
    if llm is not None:
        from brief.llm import news_gist
        for n in news:
            if n["source"] in ("bwf", "bwfworldtour"):
                try:
                    n["gist"] = news_gist(llm, n["title"])
                except Exception as e:  # noqa: BLE001
                    if errors is not None:
                        errors.append(f"llm news: {e!r}")
                    break
    g3_lines, g3_shown = grade3_section(con, g3)
    weekly = nickname.weekly_lines(con, today)
    notes = reminders(con, today)
    if not (news or g3_lines or weekly or notes):
        return None, g3, news
    lines = [f"**羽球晨報 {today.isoformat()}**"] + notes
    if g3_lines:
        lines += ["", "__**IC／IS 精選**__"] + g3_lines
    if len(g3) - g3_shown:
        lines += ["", f"另有 IC／IS 共 {len(g3) - g3_shown} 場，已存入資料庫。"]
    if news:
        lines += ["", "__**新聞**__（只當資訊來源，引用要改寫並附出處）"]
        for n in news:
            lines.append(f"- 【{NEWS_SOURCE.get(n['source'], n['source'])}】{n['title']}"
                         f"（{(n['published'] or '')[:10]}） <{n['url']}>")
            if n.get("gist"):
                lines.append(f"  　重點：{n['gist']}")
    lines += weekly
    return "\n".join(lines), g3, news


def _sent_players(con, tournament_id: int) -> set[int]:
    return {r[0] for r in con.execute(
        """SELECT DISTINCT pl FROM (
             SELECT p.player_a_id AS pl, m.tournament_id FROM digest_item d JOIN match m USING (match_id)
             JOIN pairing p ON p.pairing_id IN (m.side1_id, m.side2_id)
             UNION SELECT p.player_b_id, m.tournament_id FROM digest_item d JOIN match m USING (match_id)
             JOIN pairing p ON p.pairing_id IN (m.side1_id, m.side2_id))
           WHERE tournament_id = ? AND pl IS NOT NULL""", (tournament_id,))}


def grade3_section(con, matches: list[dict]) -> tuple[list[str], int]:
    """IC/IS 的例外（見 brief/grade3.py）。回傳（行, 列出的比賽場數）。"""
    by_t: dict[int, list[dict]] = defaultdict(list)
    for m in matches:
        if m["team_tie_id"] is None:
            by_t[m["tournament_id"]].append(m)
    lines, shown = [], set()
    for tid in sorted(by_t):
        ms = by_t[tid]
        name, level = ms[0]["tournament"], ms[0]["level"]
        line = grade3.podium_line(name, level, ms)
        if line:
            lines.append(line)
        already = _sent_players(con, tid)
        seen = set()
        for m in sorted(ms, key=lambda m: (m["date"], m["match_id"])):
            for side in (m["winner"], m["loser"]):
                for pid, pname, country in side["players"]:
                    if pid in seen or pid in already:
                        continue
                    seen.add(pid)
                    reason = grade3.notable_reason(con, pid, m["date"])
                    if reason:
                        lines.append(f"- 值得一提：{reason}的 {pname}（{zh.country(country)}）出現在 "
                                     f"{zh.tournament(name)}（{zh.level(level)}）")
                        mine = [x for x in ms if pid in {p[0] for p in x["winner"]["players"] + x["loser"]["players"]}]
                        last = max(mine, key=lambda x: (x["date"], -_sort_key(x)[0], x["match_id"]))   # 22:45：只列當天最後一場
                        lines.append("  " + _line(last))
                        shown.add(last["match_id"])
    return lines, len(shown)


def mark_sent(con, today: dt.date, matches: list[dict], ties: list[dict], news: list[dict] = ()) -> None:
    con.executescript(DIGEST_TABLE + NEWS_TABLE)          # brief.watch 可能在日報之前先跑
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
    ap.add_argument("--include-sent", action="store_true", help="包含已推送過的項目（重新測試格式用）")
    ap.add_argument("--llm", action="store_true", help="用例行模型加上「今日重點」與新聞重點")
    a = ap.parse_args()
    today = dt.date.fromisoformat(a.date) if a.date else today_taipei()
    con = connect(a.db)
    model = None
    if a.llm:
        from brief import llm
        model = llm.AnthropicLLM("routine", con=con)
    errors: list[str] = []
    text, matches, ties, news = build(con, today, llm=model, errors=errors, include_sent=a.include_sent)
    for e in errors:
        print("錯誤：", e, file=__import__("sys").stderr)
    if not a.send:
        print(text)
        return
    discord.send(discord.webhook("DISCORD_WEBHOOK_DAILY"), text)
    mark_sent(con, today, matches, ties, news)
    print(f"已推送：{len(matches)} 場、{len(ties)} 場團體對戰、{len(news)} 則新聞")


if __name__ == "__main__":
    main()
