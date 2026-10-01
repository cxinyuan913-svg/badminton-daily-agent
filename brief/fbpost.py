"""FB 粉專貼文草稿（交接單 006）：每天台灣時間 07:00 產 1 則，Raymond 看過自己貼。不串 Meta API、不自動發文。

選題（第一個成立的就用）：
  1. 台灣戰報：昨天（台灣時間）有推送層級賽事、而且有台灣選手出賽 → 昨天的結果（含差一點）＋今天的對手與台灣時間
     ＋當天跟台灣有關、分數最高的故事濃縮成一段
  2. 故事貼文：昨天有比賽但沒有台灣選手 → 故事引擎（交接單 005）分數最高的故事
  3. 冷知識：沒有比賽的日子 → 週二有新排名就寫排名變化；否則輪流「歷史上的今天」與「宿敵／宰制」
     （規則與賽制要等 config/trivia_rules.md 由 Raymond 審過才用，在那之前跳過）

推送：`FBPOST=1` 且有 `DISCORD_WEBHOOK_FBPAGE` 才推；預設 dry，只寫檔 data/posts/YYYY-MM-DD.md。
模型：effort low；台灣戰報用 heavy（Opus，Sonnet 寫不進 400 字），其餘 routine（Sonnet）；計入每日預算，超過就跳過並告警。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path

from brief import grade3, story, storylines as sl, zh
from brief.llm import _env

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "posts"
TRIVIA_RULES = ROOT / "config" / "trivia_rules.md"
BODY_RANGE = (150, 400)
BODY_RANGE_TAIWAN = (150, 500)      # notes 20:20（Raymond 選 A）：大賽台灣選手多，戰報放寬到 500 字
MAX_EMOJI = 5
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⭐⬆⬇↔-⇿]")

SYSTEM = """你是台灣羽球粉專的小編，審稿人是前職業選手 Raymond。用繁體中文、台灣用語寫一則 FB 貼文草稿。
格式：
- 第一行就是鉤子（數字或反差），不要用「大家好」開場
- 正文 150–400 字（台灣戰報可到 500 字；不含 hashtag），短段落（每段 1–3 句，段落之間空一行），表情符號整則最多 5 個
- **正文字數（不含空白）硬上限 400 字（台灣戰報 500 字），超過會被退回**；台灣選手很多時，挑最重要的 3–4 組寫，其餘一句帶過
- 結尾一個互動問句
- hashtag 3–5 個：#羽球 加上相關選手或賽事（中文，不能有空格；外國選手沒有中文名就不要做成 hashtag）
硬性規則：
- 名字、國家、排名、比分、交手紀錄、日期、名次只能用「事實清單」的內容，照抄不推測；名字照事實清單的寫法，同一人只能一種寫法，不要自己翻譯或音譯
- 比分照事實清單（勝方在前）；主詞是敗方時倒過來寫；不准換角度重講同一個比分
- 「爆冷／冷門」只能用在事實清單標了「規則判定爆冷」的場次；「逆轉」只能用在寫到逆轉的場次
- 只有事實清單寫到金牌／銀牌／銅牌的賽事才能用獎牌字眼；World Tour 等賽事寫冠軍、亞軍、四強
- 不要自己算出事實清單沒有的新數字
- 事實清單以外的背景可以寫，但每句句尾加「⚠️推測」、語氣用「可能、大約」，並列進 todo；正文盡量少用推測
- 事實清單有「新聞」的，把標題與網址放進 first_comment（建議放在第一則留言），不要塞在正文
只輸出 JSON：{"body": "貼文正文（不含 hashtag）", "hashtags": ["#羽球", "…"], "image": "建議配圖：版型＋要填的欄位",
"first_comment": ["新聞標題 網址"], "todo": [{"claim": "推測內容", "basis": "依據", "how": "建議怎麼查"}]}"""


# ---------------------------------------------------------------- 選題
def _tournaments_on(con, day: str) -> list[dict]:
    rows = con.execute(
        f"""SELECT DISTINCT t.tournament_id, t.name, t.level, t.start_date, t.end_date FROM match m JOIN tournament t USING (tournament_id)
            WHERE m.match_date=? AND m.winner_side IN (1, 2) AND t.level IS NOT NULL
              AND t.level NOT IN ({",".join("?" * len(grade3.NOT_PUSH_LEVELS))})
            ORDER BY t.level, t.tournament_id""", (day, *grade3.NOT_PUSH_LEVELS)).fetchall()
    return [dict(zip(["tournament_id", "name", "level", "start_date", "end_date"], r)) for r in rows]


def _today_opponents(con, t: dict, today: str) -> list[str]:
    """今天（台灣時間）台灣選手的對手與開打時間：用資料庫的賽程欄位（對戰、match_time_utc），不放賽果。"""
    out = []
    rows = con.execute("SELECT match_id FROM match WHERE tournament_id=? AND match_date=? AND team_tie_id IS NULL",
                       (t["tournament_id"], today)).fetchall()
    from brief import digest
    for m in digest.stage_matches(con, t["tournament_id"], [r[0] for r in rows]):
        sides = [m["winner"], m["loser"]]
        tpe = [s for s in sides if s["home"]]
        if not tpe:
            continue
        opp = [s for s in sides if not s["home"]]
        when = con.execute("SELECT match_time_utc FROM match WHERE match_id=?", (m["match_id"],)).fetchone()[0]
        tw = ""
        if when:
            tw = (dt.datetime.fromisoformat(when.replace("Z", "")[:19]) + dt.timedelta(hours=8)).strftime("%H:%M")
        vs = f"{sl.side_text(opp[0])}" if opp else "台灣選手內戰：" + "、".join(s["name"] for s in sides)
        ev = zh.EVENT.get(m["event"], m["event"])
        out.append(f"今天 {ev} {zh.round_name(m['round'])}：{tpe[0]['name']} 對 {vs}" + (f"，台灣時間 {tw}" if tw else ""))
    return out


def key_tpe_lines(lines: list[str], limit: int = 6) -> list[str]:
    """台灣選手很多時只留重點（貼文 400 字放不下全部）：輸球、差一點、贏有排名的對手優先，其餘照順序。"""
    def weight(line: str) -> int:
        winner, _, loser = line.split("，比分")[0].partition("勝 ")
        return (3 if "中華台北" in loser else 0) + (2 if "差一點" in line else 0) + (1 if "#" in loser else 0)
    keep = sorted(range(len(lines)), key=lambda i: -weight(lines[i]))[:limit]
    return [lines[i] for i in sorted(keep)]


def choose(con, today: str) -> tuple[str, list[str], dict]:
    """回傳（類型, 事實清單, 補充資訊）。today = 台灣時間的日期。"""
    y = (dt.date.fromisoformat(today) - dt.timedelta(days=1)).isoformat()
    ts = _tournaments_on(con, y)
    tpe_ts = []
    for t in ts:
        ms = [m for m in sl.tournament_matches(con, t["tournament_id"], y) if m["date"] == y]
        if any(m["winner"]["home"] or m["loser"]["home"] for m in ms):
            tpe_ts.append((t, ms))
    if tpe_ts:
        facts = []
        for t, ms in tpe_ts:
            facts.append(f"賽事：{zh.tournament(t['name'])}（{zh.level(t['level'])}），昨天是 {y}")
            tw = [m for m in ms if m["winner"]["home"] != m["loser"]["home"]]      # 不算台灣內戰
            won = sum(1 for m in tw if m["winner"]["home"])
            facts.append(f"昨天台灣選手對外國選手 {won} 勝 {len(tw) - won} 負（共 {len(tw)} 場；台灣內戰另計）")
            facts += ["【昨天台灣選手】"] + key_tpe_lines(sl.taiwan_facts(con, t, ms, whole=False), limit=5)
            opp = _today_opponents(con, t, today)
            if opp:
                facts += ["【今天的對手】"] + opp[:4]
            cands, daym, _ = story.day_candidates(con, t, y)
            tpe_story = next((c for c in story.pick(cands, threshold=0) if story._tpe(c)), None)
            if tpe_story:
                facts += ["【跟台灣有關的故事】"] + tpe_story["facts"]
        return "taiwan", list(dict.fromkeys(facts)), {"tournaments": [t["name"] for t, _ in tpe_ts]}
    if ts:
        best = None
        for t in ts:
            cands, daym, allm = story.day_candidates(con, t, y)
            picks = story.pick(cands)
            if picks and (best is None or picks[0]["final"] > best[1]["final"]):
                best = (t, picks[0], cands, daym, allm)
        if best:
            t, c, cands, daym, allm = best
            return "story", story.materials(con, t, y, c, cands, daym, allm), {"kind": c["kind"], "score": c["final"]}
    return trivia(con, today)


def trivia(con, today: str) -> tuple[str, list[str], dict]:
    d = dt.date.fromisoformat(today)
    if d.weekday() == 1:                                   # 週二：有新排名就寫排名變化
        from brief import script
        w = script.weekly_facts(con)
        if w and w["week"] >= (d - dt.timedelta(days=2)).isoformat():
            return "ranking", w["facts"], {"week": w["week"]}
    kinds = ["history", "rivalry"] + (["rules"] if checked_rules() else [])
    start = d.toordinal() % len(kinds)
    for kind in kinds[start:] + kinds[:start]:          # 輪流，當天的那一類沒素材就換下一類
        facts = {"history": history_today, "rivalry": recent_rivalry, "rules": rules_trivia}[kind](con, d)
        if facts:
            return kind, facts, {}
    return "none", [], {}


def load_trivia_rules(path: Path | None = None) -> list[dict]:
    """config/trivia_rules.md：「## [x] 1. 標題」＋條列內容。回傳每條 {no, title, checked, lines}。"""
    path = path or TRIVIA_RULES
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    out = []
    for m in re.finditer(r"^## \[( |x|X)\] (\d+)\. (.+?)\n(.*?)(?=^## |\Z)", text, re.M | re.S):
        mark, no, title, body = m.groups()
        lines = [l.strip()[2:].strip() for l in body.splitlines() if l.strip().startswith("- ")]
        out.append({"no": int(no), "title": title.strip(), "checked": mark.lower() == "x", "lines": lines})
    return out


def checked_rules(path: Path | None = None) -> list[dict]:
    """只有 Raymond 勾成 [x] 的條目可以用（notes 20:20）。"""
    return [r for r in load_trivia_rules(path) if r["checked"]]


def rules_trivia(con, d: dt.date) -> list[str]:
    rules = checked_rules()
    if not rules:
        return []
    r = rules[d.toordinal() % len(rules)]
    return [f"今天是 {d.isoformat()}，規則與賽制冷知識（config/trivia_rules.md 第 {r['no']} 條，Raymond 審過）：{r['title']}"] + r["lines"]


def history_today(con, d: dt.date) -> list[str]:
    """資料庫裡同月同日（2017 年起）的大賽決賽與台灣選手奪冠。"""
    md = d.strftime("-%m-%d")
    rows = con.execute(
        """SELECT m.match_id, m.tournament_id, m.match_date, t.level FROM match m JOIN tournament t USING (tournament_id)
           WHERE substr(m.match_date, 5)=? AND m.round IN ('Final', 'F') AND m.winner_side IN (1, 2) AND m.team_tie_id IS NULL
             AND m.match_date >= '2017-01-01' AND m.match_date < ? ORDER BY m.match_date""", (md, d.isoformat())).fetchall()
    from brief import digest
    big, tpe = [], []
    for mid, tid, date, level in rows:
        m = digest.stage_matches(con, tid, [mid])[0]
        m = sl.official_only(m)
        name = zh.tournament(con.execute("SELECT name FROM tournament WHERE tournament_id=?", (tid,)).fetchone()[0])
        years = d.year - int(date[:4])
        line = f"{years} 年前的今天（{date}），{name} " + sl.match_fact(m)
        if m["winner"]["home"]:
            tpe.append(line + "（中華台北選手奪冠）")
        elif level in ("G1_IND", "WTF", "S1000", "MULTI"):
            big.append(line)
    facts = tpe[:3] + big[:3]
    return [f"今天是 {d.isoformat()}，歷史上的今天（資料庫 2017 年以來）："] + facts if facts else []


def recent_rivalry(con, d: dt.date) -> list[str]:
    """最近 3 個月內有交手的宿敵／宰制候選，取分數最高的一則。"""
    since = (d - dt.timedelta(days=90)).isoformat()
    from brief import digest
    best = None
    rows = con.execute(
        """SELECT m.match_id, m.tournament_id FROM match m JOIN tournament t USING (tournament_id)
           WHERE m.match_date BETWEEN ? AND ? AND m.round IN ('Final', 'F', 'SF') AND m.winner_side IN (1, 2)
             AND m.team_tie_id IS NULL AND t.level IN ('G1_IND', 'WTF', 'S1000', 'S750', 'MULTI', 'CONT_IND')""",
        (since, d.isoformat())).fetchall()
    for mid, tid in rows:
        m = sl.official_only(digest.stage_matches(con, tid, [mid])[0])
        for c in sl.candidates_for_match(con, m):
            if c["kind"] in ("rivalry", "domination") and (best is None or c["score"] > best["score"]):
                best = c
    return [f"今天是 {d.isoformat()}，沒有比賽；最近 3 個月交手過的宿敵／宰制故事："] + best["facts"] if best else []


# ---------------------------------------------------------------- 產生與檢查
def body_range(kind: str) -> tuple[int, int]:
    return BODY_RANGE_TAIWAN if kind == "taiwan" else BODY_RANGE


def system_prompt() -> str:
    """貼文規則＋Raymond 的審稿準則全文（docs/video/review-guidelines.md，每次讀最新版；notes 20:35）。"""
    g = story.guidelines()
    return SYSTEM + ("\n\n以下是 Raymond 的審稿準則，貼文也要遵守（R3 的「賽果背景」在貼文裡指跟主題無關的段落）：\n" + g if g else "")


def check(out: dict, facts: list[str], kind: str = "story") -> list[str]:
    from brief.llm import medal_misuse, unlicensed_upsets, unverified
    from brief.script import rehashed_scores
    body = out.get("body") or ""
    tags = out.get("hashtags") or []
    if not body:
        return ["格式不對：沒有正文"]
    problems = []
    n = len(re.sub(r"\s", "", body))
    lo, hi = body_range(kind)
    if not lo <= n <= hi:
        problems.append(f"正文 {n} 字，不在 {lo}–{hi}")
    if body.lstrip().startswith("大家好"):
        problems.append("不要用「大家好」開場")
    if len(EMOJI.findall(body)) > MAX_EMOJI:
        problems.append(f"表情符號超過 {MAX_EMOJI} 個")
    if not 3 <= len(tags) <= 5 or "#羽球" not in tags or not all(t.startswith("#") for t in tags):
        problems.append("hashtag 要 3–5 個且包含 #羽球")
    if any(re.search(r"\s", t) for t in tags):
        problems.append("hashtag 不能有空格（「#AN Se Young」會斷掉）；外國選手用中文譯名或省略")
    if story.SPEC in body and not out.get("todo"):
        problems.append("有推測句但沒有待查清單")
    source = "\n".join(facts)
    checked = story.strip_speculation(body)
    missing = unverified(checked, source)
    if missing:
        problems.append("事實清單查不到：" + "、".join(missing[:8]))
    bad = unlicensed_upsets(checked, source)
    if bad:
        problems.append(f"「爆冷」用在規則沒判定的場次：{bad[0][:60]}")
    if "逆轉" in checked and "逆轉" not in source:
        problems.append("「逆轉」沒有對應的事實")
    if medal_misuse(checked, source):
        problems.append("這站沒有頒獎牌，不能寫金銀銅牌")
    rep = rehashed_scores(checked)
    if rep:
        problems.append("同一個比分換角度重講：" + "、".join(rep[:3]))
    return problems


def generate(llm, kind: str, facts: list[str], con=None, today: str | None = None) -> tuple[dict | None, list[str]]:
    from brief import script
    user = f"貼文類型：{KIND_LABEL[kind]}\n\n事實清單：\n" + "\n".join(f"{i + 1}. {f}" for i, f in enumerate(facts))
    if hasattr(llm, "task"):
        llm.task = "fbpost"
    problems: list[str] = []
    out: dict = {}
    for attempt in range(2):
        prompt = user if not problems else user + "\n\n上一版沒有通過檢查，請修正：" + "；".join(problems)
        n = len(re.sub(r"\s", "", out.get("body") or ""))
        hi = body_range(kind)[1]
        if n > hi:
            prompt += f"。上一版正文 {n} 字，至少要刪掉 {n - hi + 30} 字（整段刪掉次要的選手，不要只縮句子）"
        raw = llm.complete(system_prompt(), prompt)
        try:
            out = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
        except ValueError:
            out = {}
        problems = check(out, facts, kind)
        script.log_check(con, {"day": today}, f"fbpost_{kind}", llm, attempt + 1, problems)
        if not problems:
            return out, []
    return None, problems


KIND_LABEL = {"taiwan": "台灣戰報", "story": "故事貼文", "ranking": "排名變化", "history": "冷知識：歷史上的今天",
              "rivalry": "冷知識：宿敵／宰制", "rules": "冷知識：規則與賽制", "none": "（沒有素材）"}


def render(today: str, kind: str, out: dict) -> str:
    """推送格式：「貼文正文」可以整段複製；「給你的備註」只給 Raymond 看。"""
    body = out["body"].strip() + "\n\n" + " ".join(out.get("hashtags", []))
    notes = [f"類型：{KIND_LABEL[kind]}", "建議配圖：" + re.sub(r"^(建議配圖[:：]\s*)+", "", out.get("image", ""))]
    if out.get("first_comment"):
        notes.append("建議放在第一則留言：" + "；".join(out["first_comment"]))
    if out.get("todo"):
        notes.append("待查清單：" + "；".join(f"{x.get('claim', '')}（依據：{x.get('basis', '')}；怎麼查：{x.get('how', '')}）"
                                         for x in out["todo"]))
    return "\n".join([f"📣 **粉專貼文草稿｜{today}**", "", "**── 貼文正文（整段複製）──**", body, "",
                      "**── 給你的備註（不要貼）──**"] + [f"- {x}" for x in notes])


def run(con, today: str, make_llm=None, send=None, alert=None, ignore_budget: bool = False) -> dict:
    from brief import discord, script
    kind, facts, info = choose(con, today)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{today}.md"
    if kind == "none":
        path.write_text(f"# 粉專貼文草稿｜{today}\n\n（今天沒有可用素材）\n", encoding="utf-8")
        return {"status": "skipped", "kind": kind, "path": str(path)}
    if not ignore_budget and script.over_budget(con, script.taipei_today()):
        script.budget_alert(con, alert, script.taipei_today())
        return {"status": "skipped_budget", "kind": kind}
    # 台灣戰報素材多，Sonnet low 連續 4 次超過 400 字；opus low 一次通過（2026-10-01 驗收）→ 戰報暫用 heavy、其餘 routine
    purpose = "heavy" if kind == "taiwan" else "routine"
    llm = make_llm() if make_llm else script.make_script_llm(purpose, con, effort="low")
    out, problems = generate(llm, kind, facts, con, today)
    if out is None:
        path.write_text(f"# 粉專貼文草稿｜{today}\n\n（未通過事實檢查：{'；'.join(problems)}）\n", encoding="utf-8")
        if alert:
            alert(f"**粉專貼文產生失敗**｜{today}：{'；'.join(problems)[:300]}")
        return {"status": "failed", "kind": kind, "problems": problems, "facts": facts, "path": str(path)}
    text = render(today, kind, out)
    path.write_text(text + "\n\n<details><summary>事實清單</summary>\n\n" + "\n".join(f"- {f}" for f in facts)
                    + "\n\n</details>\n", encoding="utf-8")
    url = _env("DISCORD_WEBHOOK_FBPAGE")
    if _env("FBPOST") == "1" and url:
        (send or (lambda t: discord.send(url, t)))(text)
    return {"status": "ok", "kind": kind, "info": info, "out": out, "facts": facts, "path": str(path)}


def main():
    from brief import discord
    from brief.crawler import connect
    ap = argparse.ArgumentParser(description="FB 粉專貼文草稿（預設只寫檔；FBPOST=1 才推粉專頻道）")
    ap.add_argument("--db", default="data/brief.db")
    ap.add_argument("--date", help="台灣時間的「今天」（YYYY-MM-DD），預設今天")
    a = ap.parse_args()
    con = connect(a.db)
    zh.apply_player_names(con)
    from brief import script
    today = a.date or script.taipei_today()
    alert = lambda text: discord.send(discord.webhook("DISCORD_WEBHOOK_ALERTS"), text)
    res = run(con, today, alert=alert)
    print(res["status"], res.get("kind"), res.get("path", ""), res.get("problems", ""))


if __name__ == "__main__":
    main()
