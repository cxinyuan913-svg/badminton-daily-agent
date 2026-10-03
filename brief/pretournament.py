"""大賽賽前看點（10-03 Raymond）：Super 750 以上（S750、S1000、年終總決賽）與 Grade 1 個人賽，開賽前一天 18:00
產 1 篇粉專＋1 支影片：首日賽程（籤表）、種子、宿敵對戰、台灣選手的對手分析。粉專字數不限。

素材全部來自 BWF 賽程 API（開賽日的 day-matches）與資料庫（排名、交手紀錄、對手近況），不用 LLM 產生事實。
開賽日賽程還沒公布（18:00 時）→ 不產，推告警一次。每站只產一次（pretournament_sent）。費用約 US$1.5／站，不受每日預算擋下。

  python -m brief.pretournament --db data/brief.db [--today 2026-10-12] [--tournament 5210]
"""
from __future__ import annotations

import argparse
import datetime as dt

from brief import fbpost, grade3, preview, script, story, storylines as sl, verify, zh
from brief.llm import _env

LEVELS = ("G1_IND", "G1_EVENT", "WTF", "S1000", "S750")
SENT_TABLE = """CREATE TABLE IF NOT EXISTS pretournament_sent (
    tournament_id INTEGER PRIMARY KEY, status TEXT NOT NULL, at TEXT NOT NULL DEFAULT (datetime('now')))"""
RECENT = 5                          # 對手近況：最近幾站


def upcoming(con, today: str, tournament_id: int | None = None) -> list[dict]:
    """明天（台灣時間）開賽、層級符合的賽事；排除已取消。"""
    tomorrow = (dt.date.fromisoformat(today) + dt.timedelta(days=1)).isoformat()
    sql = (f"SELECT tournament_id, name, level, start_date, end_date, code FROM tournament WHERE "
           f"{'tournament_id=?' if tournament_id else 'start_date=?'} AND level IN ({','.join('?' * len(LEVELS))}) "
           f"AND code IS NOT NULL AND {grade3.ACTIVE_SQL}")
    rows = con.execute(sql, ((tournament_id,) if tournament_id else (tomorrow,)) + LEVELS).fetchall()
    return [dict(zip(["tournament_id", "name", "level", "start_date", "end_date", "code"], r)) for r in rows]


def opponent_recent(con, pairing: int, event: str, before: str) -> str | None:
    rows = con.execute("""SELECT t.name, r.round_reached, r.result_date FROM tournament_result r JOIN tournament t USING (tournament_id)
                          WHERE r.pairing_id=? AND r.event=? AND r.result_date < ? AND r.round_reached <> 'TEAM'
                          ORDER BY r.result_date DESC LIMIT ?""", (pairing, event, before, RECENT)).fetchall()
    if not rows:
        return None
    return "、".join(f"{d[:10]} {zh.tournament(n)} {sl.place_name(None, pos)}" for n, pos, d in rows)


def _real(m: dict) -> bool:
    return (not m.get("isTeamMatch") and bool(preview._ids(m.get("team1"))) and bool(preview._ids(m.get("team2"))))


def seeds(con, schedule: list[dict]) -> list[str]:
    by_event: dict[str, dict[int, str]] = {}
    for m in filter(_real, schedule):
        ev = preview.DISCIPLINE.get(m.get("matchTypeValue"), m.get("eventName"))
        for k in ("1", "2"):
            s = m.get(f"team{k}seed")
            if s and str(s).isdigit():
                team = m.get(f"team{k}")
                by_event.setdefault(ev, {})[int(s)] = preview._side_name(con, preview._ids(team), team)
    return [f"{zh.event(ev)} 種子：" + "、".join(f"{n} 號 {name}" for n, name in sorted(ss.items()))
            for ev, ss in sorted(by_event.items(), key=lambda kv: ["MS", "WS", "MD", "WD", "XD"].index(kv[0])
                                  if kv[0] in ("MS", "WS", "MD", "WD", "XD") else 9)]


def facts_for(con, t: dict, schedule: list[dict]) -> list[str]:
    day = t["start_date"]
    tracked = {int(r["player_id"]) for r in zh.load_player_table() if r.get("track") == "Y" and r["player_id"]}
    ms = [m for m in schedule if _real(m)]
    facts = [f"賽前看點：{zh.tournament(t['name'])}（{zh.level(t['level'])}），{t['start_date']}～{t['end_date']}；"
             f"首日 {day} 共 {len(ms)} 場（只列已排定雙方的個人賽）"]
    sd = seeds(con, ms)
    if sd:
        facts += ["【種子（首日賽程上看得到的）】"] + sd
    tpe = []
    for m in ms:
        why, info = preview.reasons(con, m, day, tracked)
        if not info["tpe"]:
            continue
        tpe.append(preview.line(con, m, info, why))
        opp, opp_team = (info["p1"], m.get("team1")) if info["flip"] else (info["p2"], m.get("team2"))
        if opp and "TPE" not in {p.get("countryCode") for p in (opp_team or {}).get("players") or []}:
            recent = opponent_recent(con, opp, info["event"], day)
            name = preview._side_name(con, preview._ids(opp_team), opp_team)
            if recent:
                tpe.append(f"　對手 {name} 最近 {RECENT} 站：{recent}")
    facts += ["【首日台灣選手的對戰與對手分析】"] + (tpe or ["首日沒有台灣選手出賽"])
    focus = [x for x in preview.select(con, ms, day, tracked) if x not in tpe]
    if focus:
        facts += ["【首日焦點（前 10 對決、宿敵重演、交手懸殊）】"] + focus
    return facts


def _sent(con, tid: int) -> bool:
    con.execute(SENT_TABLE)
    return con.execute("SELECT 1 FROM pretournament_sent WHERE tournament_id=? AND status='ok'", (tid,)).fetchone() is not None


def _mark(con, tid: int, status: str) -> None:
    con.execute("INSERT OR REPLACE INTO pretournament_sent (tournament_id, status) VALUES (?, ?)", (tid, status))
    con.commit()


def run(con, client, today: str | None = None, tournament_id: int | None = None, send_fb=None, send_script=None,
        alert=None, make_llm=None, force: bool = False) -> list[dict]:
    from brief import discord, watch
    today = today or script.taipei_today()
    out = []
    for t in upcoming(con, today, tournament_id):
        tid = t["tournament_id"]
        if _sent(con, tid) and not force:
            continue
        schedule = watch._fetch(client, t["code"], t["start_date"])
        if not watch.schedule_published(schedule):
            if alert and con.execute("SELECT 1 FROM pretournament_sent WHERE tournament_id=?", (tid,)).fetchone() is None:
                alert(f"**賽前看點**｜{zh.tournament(t['name'])}：{t['start_date']} 的賽程還沒公布，今天沒產")
            _mark(con, tid, "no_schedule")
            out.append({"tournament_id": tid, "status": "no_schedule"})
            continue
        facts = facts_for(con, t, schedule)
        title = f"{zh.tournament(t['name'])}｜賽前看點"
        llm = (lambda: make_llm()) if make_llm else (lambda: script.make_script_llm(
            "heavy", con, effort="medium", max_tokens=fbpost.POST_MAX_TOKENS_LONG))
        editor = make_llm() if make_llm else script.make_script_llm("heavy", con, effort="low")
        checker = None if make_llm else (lambda text: verify.verify(text, con=con))
        res = {"tournament_id": tid, "facts": facts}
        # 粉專
        post, problems = fbpost.generate(llm(), "preview", facts, con, today, 0, editor, verifier=checker)
        fbpost.OUT_DIR.mkdir(parents=True, exist_ok=True)
        path = fbpost.OUT_DIR / f"{today}_preview_{tid}.md"
        if post is None:
            path.write_text(f"# {title}\n\n（未通過事實檢查：{'；'.join(problems)}）\n", encoding="utf-8")
            if alert:
                alert(f"**賽前看點粉專產生失敗**｜{title}：{'；'.join(problems)[:300]}")
        else:
            text = fbpost.render(today, "preview", post)
            path.write_text(text + "\n\n<details><summary>事實清單</summary>\n\n" + "\n".join(f"- {f}" for f in facts)
                            + "\n\n</details>\n", encoding="utf-8")
            url = _env("DISCORD_WEBHOOK_FBPAGE")
            if send_fb or (_env("FBPOST") == "1" and url):
                (send_fb or (lambda x: discord.send(url, x)))(text)
        res["fb"] = "ok" if post else "failed"
        # 影片
        model = llm()
        if hasattr(model, "task"):
            model.task = "script_preview"
        sc, problems = story.generate(model, facts, con, {"tournament_id": tid, "day": today}, editor=editor, verifier=checker)
        spath = script.OUT_DIR / f"{today}_preview_{tid}.md"
        script.OUT_DIR.mkdir(parents=True, exist_ok=True)
        if sc is None:
            spath.write_text(f"# {title}\n\n（未通過事實檢查：{'；'.join(problems)}）\n", encoding="utf-8")
            if alert:
                alert(f"**賽前看點腳本產生失敗**｜{title}：{'；'.join(problems)[:300]}")
        else:
            spath.write_text(f"# 影片腳本草稿｜{title}\n\n" + story.to_markdown(title, {"kind": "preview", "final": 0}, sc)
                             + "\n", encoding="utf-8")
            sender = send_script or script.script_sender()
            if sender:
                sender(story.to_discord(title, sc))
        res["script"] = "ok" if sc else "failed"
        _mark(con, tid, "ok" if (post or sc) else "failed")
        out.append(res)
    return out


def main():
    from brief import discord
    from brief.crawler import Client, connect
    ap = argparse.ArgumentParser(description="大賽賽前看點（開賽前一天 18:00）")
    ap.add_argument("--db", default="data/brief.db")
    ap.add_argument("--today")
    ap.add_argument("--tournament", type=int, help="指定賽事（測試用；不看開賽日）")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    con = connect(a.db)
    zh.apply_player_names(con)
    alert = lambda text: discord.send(discord.webhook("DISCORD_WEBHOOK_ALERTS"), text)
    for r in run(con, Client(), a.today, a.tournament, alert=alert, force=a.force):
        print(r["tournament_id"], r.get("status") or f"fb={r['fb']} script={r['script']}")


if __name__ == "__main__":
    main()
