"""每站當地當天打完就發（2026-09-30 Raymond 決議，notes 18:15）

Windows 工作排程器每 30 分鐘執行一次 `python -m brief.watch --db data/brief.db`：
  1. 找出進行中的推送層級賽事（Super 100 以上、Grade 1、洲際錦標賽、綜合運動會；不含 IC／IS）
  2. 每站只抓還沒發過的「當地昨天、今天」的 day-matches（每站每次 1–2 個請求）
  3. 某一天的場次全部結束（完賽、退賽、不戰而勝；改到別天的場次本來就不在當天清單）且還沒發過 → 發一則
     保險：當地隔天 03:00 仍有沒打完的，照樣發，最後一行「未完成：N 場」
  4. 每則：標題（第 N 天、最深輪次、台灣時間幾點打完）、今日重點（LLM routine，事實檢查）、全部賽果、
     明日看點（brief.preview；決賽日改列本站冠軍）。明日賽程還沒公布 → 之後偵測到再另發一則，只發一次，
     最晚在第一場開打前
  5. 同一站連續 3 次抓不到資料 → 告警（同一站一天最多一次）
發過的記在 stage_digest；電腦睡眠錯過的，下次執行會補發。

用法：
  python -m brief.watch --db data/brief.db                  # 正式執行（推送到 DISCORD_WEBHOOK_DAILY）
  python -m brief.watch --db data/brief.db --dry-run --now 2026-09-29T15:00:00   # 只印出、不推送也不記錄
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys

from brief import crawler, digest, grade3, preview, results, zh
from brief.crawler import API, Client, connect

GRADE3 = {"IC", "IS", "FS"}
NEVER = {"FS"}                                  # 永遠不發；IC／IS 只在空檔週升格（23:15）
SKIP_STATUS = ("cancelled", "postponed")
FORCE_AT = dt.timedelta(days=1, hours=3)        # 當地隔天 03:00
FAIL_ALERT = 3

WATCH_TABLES = """
CREATE TABLE IF NOT EXISTS stage_digest (
    tournament_id   INTEGER NOT NULL,
    local_date      TEXT NOT NULL,
    sent_at         TEXT NOT NULL DEFAULT (datetime('now')),
    forced          INTEGER NOT NULL DEFAULT 0,   -- 1 = 保險發送（還有沒打完的）
    unfinished      INTEGER NOT NULL DEFAULT 0,
    has_preview     INTEGER NOT NULL DEFAULT 0,   -- 這則已含明日看點
    PRIMARY KEY (tournament_id, local_date)
);
CREATE TABLE IF NOT EXISTS stage_preview (
    tournament_id   INTEGER NOT NULL,
    local_date      TEXT NOT NULL,                -- 看點所屬的比賽日（當地）
    sent_at         TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (tournament_id, local_date)
);
CREATE TABLE IF NOT EXISTS watch_fail (
    tournament_id   INTEGER PRIMARY KEY,
    streak          INTEGER NOT NULL DEFAULT 0,
    alerted_on      TEXT                          -- 最近一次告警的台灣日期
);
"""


# ---------------------------------------------------------------- 判斷
def _finished(m: dict) -> bool:
    if m.get("isTeamMatch"):
        return m.get("winner") in (1, 2)
    return crawler.is_finished(m) or (m.get("scoreStatusValue") in ("Retired", "Walkover") and m.get("winner") in (1, 2))


def _real(m: dict) -> bool:
    """有雙方選手的場次（排除輪空）。團體賽看外層。"""
    if m.get("isTeamMatch"):
        return True
    return bool((m.get("team1") or {}).get("players")) and bool((m.get("team2") or {}).get("players"))


def day_status(day_matches: list[dict]) -> tuple[bool, int]:
    """(是否全部結束, 未完成場數)。沒有任何場次視為未結束（可能還沒公布）。"""
    real = [m for m in day_matches if _real(m)]
    unfinished = sum(not _finished(m) for m in real)
    return (bool(real) and unfinished == 0), unfinished


def local_offset(day_matches: list[dict]) -> dt.timedelta:
    for m in day_matches:
        off = preview.local_offset(m)
        if off is not None:
            return off
    return dt.timedelta(hours=8)


def finished_at_taiwan(day_matches: list[dict]) -> dt.datetime | None:
    ends = []
    for m in day_matches:
        start = preview.taiwan_time(m)
        if start and _finished(m):
            ends.append(start + dt.timedelta(minutes=int(m.get("duration") or 0)))
    return max(ends) if ends else None


def schedule_published(day_matches: list[dict]) -> bool:
    return any(_real(m) and m.get("matchStatus") == "N" and m.get("matchTimeUtc")
               and not (m.get("oopText") or "").startswith("Court and time TBA") for m in day_matches)


# ---------------------------------------------------------------- 訊息
def _deepest_round(ms: list[dict]) -> str:
    order = digest.ROUND_ORDER
    rounds = [m["round"] for m in ms if m["round"]]
    return max(rounds, key=lambda r: order.index(r) if r in order else -1) if rounds else ""


def tracked_tpe_ids() -> set[int]:
    return {int(r["player_id"]) for r in zh.load_player_table() if r.get("track") == "Y" and r["player_id"]}


def champions(stage: list[dict]) -> list[str]:
    finals = [m for m in stage if m["round"] in ("Final", "F") and m["team_tie_id"] is None]
    return [f"- {zh.event(m['event'])}：**{m['winner']['name']}**（{zh.country(m['winner']['country'])}）"
            for m in sorted(finals, key=digest._sort_key)]


def stage_message(con, t: dict, local_date: str, day_matches: list[dict], next_schedule: list[dict] | None,
                  llm=None, forced_unfinished: int = 0, errors: list | None = None) -> tuple[str, list[dict], bool]:
    """回傳（訊息, 本站本日的比賽 dict, 是否含明日看點）。"""
    ids = [int(m["id"]) for m in day_matches if not m.get("isTeamMatch") and _finished(m) and _real(m)]
    ids += [int(s["id"]) for m in day_matches if m.get("isTeamMatch") for s in m.get("matches") or []
            if _finished(s) and _real(s)]
    stage = digest.stage_matches(con, t["tournament_id"], ids)
    individual = [m for m in stage if m["team_tie_id"] is None]
    day_no = (dt.date.fromisoformat(local_date) - dt.date.fromisoformat(t["start_date"])).days + 1
    d = dt.date.fromisoformat(local_date)
    end = finished_at_taiwan(day_matches)
    when = "保險發送" if forced_unfinished else (f"台灣時間 {end.strftime('%H:%M')} 打完" if end else "已打完")
    promoted = t.get("level") in grade3.PROMOTE
    tag = f"｜{t['level']}" if promoted else ""
    head = f"**{zh.tournament(t['name'])}{tag}｜第 {day_no} 天 {zh.round_name(_deepest_round(stage))}**（當地 {d.month}/{d.day}，{when}）"

    body = []
    ties = [m for m in day_matches if m.get("isTeamMatch") and m.get("winner") in (1, 2)]
    for tie in ties:
        s = (tie.get("score") or [{}])[0]
        c1, c2 = (tie.get("team1") or {}).get("countryCode"), (tie.get("team2") or {}).get("countryCode")
        w = c1 if tie["winner"] == 1 else c2
        body.append(f"- {zh.competition(tie.get('eventName'))} {zh.round_name(tie.get('roundName'))}："
                    f"{zh.country(c1)} {s.get('home')}–{s.get('away')} {zh.country(c2)}（{zh.country(w)}勝）")
    hidden = 0
    if promoted:            # 空檔週的 IC／IS：八強以前只列中華台北與爆冷，八強起全部列（23:15）
        shown = [m for m in individual if m["round"] in digest.LATE_ROUNDS or m["upset"]
                 or m["winner"]["home"] or m["loser"]["home"]]
        hidden, individual = len(individual) - len(shown), shown
    body += [digest._line(m) for m in sorted(individual, key=digest._sort_key)]

    lines = [head]
    if llm is not None and body:
        from brief.llm import highlight
        try:
            lines.append(highlight(llm, "\n".join(body)).replace("**今日重點**（AI 整理，請審稿）\n", "今日重點（AI 整理，請審稿）："))
        except Exception as e:  # noqa: BLE001
            if errors is not None:
                errors.append(f"llm: {e!r}")
    empty = "（八強以前沒有中華台北選手或爆冷場次）" if promoted and hidden else "（沒有已完成的場次）"
    lines += ["", "__賽果__"] + (body or [empty])

    has_preview = False
    if local_date == t["end_date"]:
        champs = champions(stage)
        if champs:
            lines += ["", "__本站冠軍__"] + champs
    elif next_schedule is not None and schedule_published(next_schedule):
        picks = preview.select(con, next_schedule, local_date, tracked_tpe_ids())
        lines += ["", "__明日看點__"] + ([f"- {p}" for p in picks] or ["- （沒有符合選場規則的對戰）"])
        has_preview = True
    if hidden:
        lines += ["", f"另有 {hidden} 場未列，已存入資料庫"]
    if forced_unfinished:
        lines += ["", f"未完成：{forced_unfinished} 場（之後打完的併到下一天）"]
    return "\n".join(lines), stage, has_preview


def preview_message(con, t: dict, local_date: str, schedule: list[dict]) -> str:
    d = dt.date.fromisoformat(local_date)
    picks = preview.select(con, schedule, local_date, tracked_tpe_ids())
    return "\n".join([f"**{zh.tournament(t['name'])}｜明日看點**（當地 {d.month}/{d.day}）"]
                     + ([f"- {p}" for p in picks] or ["- （沒有符合選場規則的對戰）"]))


# ---------------------------------------------------------------- 流程
def targets(con, tw_today: dt.date) -> list[dict]:
    rows = con.execute(
        f"""SELECT tournament_id, code, name, level, start_date, end_date, status, source_url FROM tournament
            WHERE start_date <= ? AND end_date >= ? AND COALESCE(status, '') NOT IN ({",".join("?" * len(SKIP_STATUS))})
              AND COALESCE(level, '') NOT IN ({",".join("?" * len(NEVER))}) AND code IS NOT NULL
            ORDER BY start_date, tournament_id""",
        ((tw_today + dt.timedelta(days=1)).isoformat(), (tw_today - dt.timedelta(days=2)).isoformat(),
         *SKIP_STATUS, *sorted(NEVER))).fetchall()
    keys = ["tournament_id", "code", "name", "level", "start_date", "end_date", "status", "source_url"]
    return [dict(zip(keys, r)) for r in rows]


def _fetch(client, code: str, day: str) -> list[dict]:
    r = client.get(f"{API}/tournaments/day-matches", tournamentCode=code, date=day, order=2, court=0)
    return r.json() if r is not None else []


def run(con, client, now_utc: dt.datetime, send, alert=None, llm=None, dry_run: bool = False) -> dict:
    """send(text) 推送一則；alert(text) 推送告警。回傳統計。"""
    con.executescript(WATCH_TABLES)
    zh.apply_player_names(con)                   # 台灣選手中文名（config/players_zh.csv）
    tw_now = now_utc + preview.TAIPEI
    tw_today = tw_now.date()
    sent, previews, errors = [], [], []
    for t in targets(con, tw_today):
        tid = t["tournament_id"]
        done = {r[0] for r in con.execute("SELECT local_date FROM stage_digest WHERE tournament_id=?", (tid,))}
        days = [d for d in crawler.dates_between(max(t["start_date"], (tw_today - dt.timedelta(days=2)).isoformat()),
                                                 min(t["end_date"], tw_today.isoformat())) if d not in done]
        if t["level"] in grade3.PROMOTE:
            days = [d for d in days if grade3.quiet_week(con, d)]    # 有推送層級賽事的週，IC／IS 只在晨報列例外
            if not days:
                continue
        try:
            fetched = {d: _fetch(client, t["code"], d) for d in days}
        except Exception as e:  # noqa: BLE001
            errors.append(f"watch {tid}: {e!r}")
            _fail(con, tid, tw_today, t, alert, dry_run)
            continue
        if days and not any(fetched.values()):
            _fail(con, tid, tw_today, t, alert, dry_run)
        else:
            con.execute("DELETE FROM watch_fail WHERE tournament_id=?", (tid,))
        for day, ms in fetched.items():
            if not ms:
                continue
            crawler.store_day(con, tid, ms)
            con.commit()
            results.compute(con, tid)
            complete, unfinished = day_status(ms)
            local_now = now_utc + local_offset(ms)
            forced = not complete and local_now >= dt.datetime.fromisoformat(day) + FORCE_AT
            if not (complete or forced):
                continue
            nxt = None
            if day < t["end_date"]:
                nxt = _fetch(client, t["code"], (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat())
            text, stage, has_preview = stage_message(con, t, day, ms, nxt, llm, unfinished if forced else 0, errors)
            if not dry_run:
                send(text)
                con.execute("INSERT OR REPLACE INTO stage_digest (tournament_id, local_date, forced, unfinished, has_preview) "
                            "VALUES (?,?,?,?,?)", (tid, day, int(forced), unfinished if forced else 0, int(has_preview)))
                if has_preview:
                    con.execute("INSERT OR IGNORE INTO stage_preview (tournament_id, local_date) VALUES (?, ?)",
                                (tid, (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat()))
                digest.mark_sent(con, tw_today, stage, [])
                _scripts(con, t, day, stage, nxt, alert, errors)
            sent.append({"tournament_id": tid, "local_date": day, "forced": forced, "text": text})
        previews += _pending_previews(con, client, t, now_utc, send, dry_run)
    con.commit()
    if not dry_run:
        errors += _news(con, client)
    return {"sent": sent, "previews": previews, "errors": errors}


def _news(con, client) -> list[str]:
    """新聞列表每 30 分鐘收一次（notes 16:00）：每來源 1 個請求，已收過的網址跳過；新的台灣羽球新聞抓內文存起來。"""
    from brief import foreign_names, news
    try:
        _, errs = news.collect(client, con)
        foreign_names.scan_new(con, client, limit=5)
        return errs
    except Exception as e:  # noqa: BLE001
        return [f"news: {e!r}"]


def _scripts(con, t: dict, day: str, stage: list[dict], nxt, alert, errors: list) -> None:
    """口播腳本（notes 10:15／10:20）：.env 開關有開才產生；失敗不影響賽果推送。"""
    from brief import script
    try:
        styles = script.wanted_styles(t, day, _deepest_round(stage))
        if not styles:
            return
        picks = preview.select(con, nxt, day, tracked_tpe_ids()) if nxt and schedule_published(nxt) else None
        script.run_for_day(con, t, day, _deepest_round(stage), picks, alert=alert, styles=styles)
    except Exception as e:  # noqa: BLE001
        errors.append(f"script {t['tournament_id']} {day}: {e!r}")


def _pending_previews(con, client, t: dict, now_utc: dt.datetime, send, dry_run: bool) -> list[dict]:
    """賽果已發、但當時明日賽程還沒公布的站：偵測到公布就另發看點（只發一次，最晚在第一場開打前）。"""
    out = []
    rows = con.execute(
        """SELECT s.local_date FROM stage_digest s LEFT JOIN stage_preview p
             ON p.tournament_id = s.tournament_id AND p.local_date = date(s.local_date, '+1 day')
           WHERE s.tournament_id=? AND s.has_preview=0 AND p.local_date IS NULL AND s.local_date < ?""",
        (t["tournament_id"], t["end_date"])).fetchall()
    for (day,) in rows:
        nxt = (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat()
        sched = _fetch(client, t["code"], nxt)
        if not schedule_published(sched):
            continue
        firsts = [preview.taiwan_time(m) for m in sched if preview.taiwan_time(m)]
        if firsts and now_utc + preview.TAIPEI >= min(firsts):
            if not dry_run:           # 已經開打，不再發看點
                con.execute("INSERT OR IGNORE INTO stage_preview (tournament_id, local_date) VALUES (?, ?)",
                            (t["tournament_id"], nxt))
            continue
        text = preview_message(con, t, nxt, sched)
        if not dry_run:
            send(text)
            con.execute("INSERT OR IGNORE INTO stage_preview (tournament_id, local_date) VALUES (?, ?)",
                        (t["tournament_id"], nxt))
        out.append({"tournament_id": t["tournament_id"], "local_date": nxt, "text": text})
    return out


def _fail(con, tid: int, tw_today: dt.date, t: dict, alert, dry_run: bool) -> None:
    con.execute("INSERT INTO watch_fail (tournament_id, streak) VALUES (?, 1) "
                "ON CONFLICT(tournament_id) DO UPDATE SET streak = streak + 1", (tid,))
    streak, alerted = con.execute("SELECT streak, alerted_on FROM watch_fail WHERE tournament_id=?", (tid,)).fetchone()
    if streak >= FAIL_ALERT and alerted != tw_today.isoformat() and alert is not None and not dry_run:
        alert(f"**brief.watch** 連續 {streak} 次抓不到 {zh.tournament(t['name'])}（{tid}）的賽果")
        con.execute("UPDATE watch_fail SET alerted_on=? WHERE tournament_id=?", (tw_today.isoformat(), tid))
    con.commit()


def main():
    from brief import discord, llm
    ap = argparse.ArgumentParser(description="每站當地當天打完就發")
    ap.add_argument("--db", default="brief.db")
    ap.add_argument("--dry-run", action="store_true", help="只印出，不推送、不記錄")
    ap.add_argument("--now", help="模擬現在時間（UTC，ISO 格式），測試用")
    ap.add_argument("--no-llm", action="store_true")
    a = ap.parse_args()
    now = dt.datetime.fromisoformat(a.now) if a.now else dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    con = connect(a.db)
    model = None if a.no_llm or not llm.available() else llm.AnthropicLLM("routine", con=con)
    send = (lambda text: print(text + "\n" + "-" * 40)) if a.dry_run else \
        (lambda text: discord.send(discord.webhook("DISCORD_WEBHOOK_DAILY"), text))
    alert = lambda text: discord.send(discord.webhook("DISCORD_WEBHOOK_ALERTS"), text)
    res = run(con, Client(), now, send, alert, model, a.dry_run)
    if a.dry_run:
        for s in res["sent"] + res["previews"]:
            print(s["text"] + "\n" + "-" * 40)
    print(f"發送 {len(res['sent'])} 則賽果、{len(res['previews'])} 則看點", file=sys.stderr)
    for e in res["errors"]:
        print("錯誤：", e, file=sys.stderr)
    sys.exit(1 if res["errors"] else 0)


if __name__ == "__main__":
    main()
