"""口播短影音腳本（交接單 003；notes 10:15 多風格、10:20 每天＋每週排名）

時機（觸發點在 brief.watch 發完該站該天訊息之後；每週排名在 brief.daily 存完新一週排名之後）：
  決賽日         quick 快報、story 單一故事、taiwan 台灣視角（有台灣選手才產生）、numbers 數據型（觀察中，故事不足 3 則不產生）
  八強起的比賽日 daily_taiwan、daily_story、daily_quick（只講當天的輪次）
  更早的比賽日   daily_taiwan、daily_story
  每週二         weekly_rank 排名更新
模型：決賽日 1 快報、2 單一故事與排名更新用 heavy（opus），3 台灣視角、4 數據型與每日用 routine（sonnet）；每次呼叫記入 llm_call（task = script_<風格>）。
開關（.env，預設全關，關著就不產生、不花錢）：SCRIPT_FINAL／SCRIPT_DAILY／SCRIPT_WEEKLY
  1    產生、寫檔（data/scripts/，不進版控），推到腳本專用頻道 DISCORD_WEBHOOK_SCRIPTS（notes 10:25；不推日報頻道）
  dry  只產生、寫檔，不推
webhook 沒設定時 1 也等於 dry。費用：週一晨報列「上週腳本費用」；前一天超過 DAILY_COST_ALERT 推告警。

事實檢查（每份各自跑，不通過只丟那一份）：數字（比分整組比對）、人名都要在事實清單裡；
「爆冷」只能用在規則判定的場次、「逆轉」只能用在逆轉的故事、「首冠／首座」只能用在 first_title；
口播總長 120–320 字（素材夠就約 60 秒，不夠就 30–45 秒，notes 15:40）；同一個比分不能換角度重講；
非獎牌賽事不能出現金銀銅牌；外國選手名字只能是事實清單的寫法。不通過重試一次，再不過就記錯誤、不發。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path

from brief import digest, storylines as sl, zh
from brief.llm import _env, third_place_word, unlicensed_upsets, unverified

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "scripts"
EXAMPLES = ROOT / "tests" / "fixtures" / "script_styles"
DAILY_STORY_MIN = 6.0         # 每日單一故事的分數門檻：低於就不產生（寧缺勿濫）
VOICE_RANGE = (120, 320)      # 30 秒約 120 字；素材不夠就短，不湊秒數（notes 15:40）
DAILY_COST_ALERT = 0.5      # US$／天（notes 10:20）
# 腳本的推理強度與輸出上限（notes 15:20）：一份腳本＋3 標題實際約 1,000 token，high 的內部推理是主要費用
SCRIPT_EFFORT = {"heavy": "medium", "routine": "low"}
SCRIPT_MAX_TOKENS = 4000
DEFAULT_DAILY_BUDGET = 1.0  # US$／台北時間一天；.env 的 LLM_DAILY_BUDGET_USD 覆寫

CHECK_TABLE = """
CREATE TABLE IF NOT EXISTS script_check (
    checked_at      TEXT NOT NULL DEFAULT (datetime('now')),
    tournament_id   INTEGER,
    local_date      TEXT,
    style           TEXT NOT NULL,
    model           TEXT,
    effort          TEXT,
    attempt         INTEGER NOT NULL,             -- 1 = 第一次、2 = 重試
    passed          INTEGER NOT NULL,
    problems        TEXT                          -- 不通過的原因（哪個數字或名字對不上）
);
"""
QUICK_TPE_ROUNDS = {"R16", "QF", "SF", "Final", "F"}   # 快報的台灣段落：16 強起被淘汰的那場（主場賽事台灣選手很多）

STYLES = {
    "quick": ("快報型", "quick.md", "heavy", "SCRIPT_FINAL"),
    "story": ("單一故事型", "story.md", "heavy", "SCRIPT_FINAL"),
    "taiwan": ("台灣視角型", "taiwan.md", "routine", "SCRIPT_FINAL"),       # notes 13:35（A）：3、4 改 routine
    "numbers": ("數據型（觀察中）", "numbers.md", "routine", "SCRIPT_FINAL"),
    "daily_taiwan": ("台灣視角（每日）", "taiwan.md", "routine", "SCRIPT_DAILY"),
    "daily_story": ("單一故事（每日）", "story.md", "routine", "SCRIPT_DAILY"),
    "daily_quick": ("快報（當天輪次）", "quick.md", "routine", "SCRIPT_DAILY"),
    "weekly_rank": ("排名更新", None, "heavy", "SCRIPT_WEEKLY"),
    "story_main": ("故事", None, "heavy", None),            # 交接單 005：旗標依當天是不是決賽日（flag_for）
}

STYLE_GUIDE = {
    "quick": "五個項目的冠軍一次看：開場 0–4 秒用分數最高的故事一句話（含一個數字）；接著約 10 秒台灣段落；"
             "再依故事分數排五項冠軍，有故事的多講、沒故事的一句帶過；結尾約 6 秒互動問句＋下一站預告（事實清單有下一站才講）。",
    "story": "只講一個故事（事實清單第一則），從開場鉤子、過程到結果，結尾一句互動問句。",
    "taiwan": "整站台灣選手的成績：獎牌、最佳成績、「差一點」的場次；結尾問觀眾最想看哪一組復仇。",
    "numbers": "三個數字看懂這一天：每個數字一段，數字一定要來自事實清單。",
    "daily_taiwan": "今天台灣選手的全部戰果（含差一點的場次），以及明天的對手與台灣時間（事實清單有才講）。",
    "daily_story": "只講今天分數最高的那個故事，結尾一句互動問句。",
    "daily_quick": "只講今天打完的這一輪：各項目的結果一次看，結尾預告明天。",
    "weekly_rank": "本週世界排名更新：先講台灣選手的排名變化，再講各項目前 10 名誰進誰出，再講前 50 名內升最多的，結尾一句互動問句。",
}

SYSTEM = """你是羽球短影音的腳本作者，觀眾是台灣的羽球愛好者，審稿人是前職業選手 Raymond。
用繁體中文、台灣用語寫一支口播腳本：素材夠就約 60 秒（口播 240–280 字）；素材不夠就寫短，30–45 秒（120–200 字）也可以。
硬性規則：
- 只能使用「事實清單」裡的內容：名字、國家、排名、比分、交手紀錄、名次一律照抄，不可推測或補充
- 名字照事實清單的寫法（有中文就用中文，只有英文就用英文），絕對不要自己翻譯或音譯；同一個人整支腳本只能有一種寫法（範例裡的名字寫法不算數）
- 不准換角度重講同一個比分來湊秒數（例如先說「21 比 11」再說「從對手角度是 11 比 21」）
- 比分照事實清單（勝方在前）；句子主詞是敗方時，比分倒過來寫成主詞的角度
- 名次可以寫金牌／銀牌／銅牌（冠軍＝金、亞軍＝銀、四強＝銅）；不要用「季軍」「第三名」
- 「爆冷／冷門」只能用在事實清單標了「規則判定爆冷」的場次；「逆轉」只能用在事實清單寫到逆轉的場次；「首冠／首座」只能用在事實清單寫到「第一座」的選手
- 標題 3 個：一個具體數字或排名反差＋問句或驚嘆；或一句話總結當天最大的反差
- 範例只示範格式與語氣，範例裡的名字與數字不能用
只輸出 JSON：{"titles": ["…", "…", "…"], "segments": [{"time": "0–4s", "voice": "口播", "card": "字卡或畫面"}]}"""


# ---------------------------------------------------------------- 事實清單
def _next_tournament(con, t: dict) -> str | None:
    row = con.execute(
        """SELECT name FROM tournament WHERE start_date > ? AND COALESCE(level, '') NOT IN ('IC', 'IS', 'FS')
           AND level IS NOT NULL AND COALESCE(status, '') NOT IN ('cancelled', 'postponed') ORDER BY start_date LIMIT 1""",
        (t["end_date"],)).fetchone()
    return zh.tournament(row[0]) if row else None


def facts_for(con, t: dict, day: str, style: str, next_preview: list[str] | None = None) -> dict | None:
    """回傳 {facts: [...], flags: {...}}；這種風格不適用（例：沒有台灣選手）時回傳 None。"""
    allm = sl.tournament_matches(con, t["tournament_id"], day)
    daym = [m for m in allm if m["date"] == day]
    if not daym:
        return None
    stories = sl.stories_for(con, t, day, daym, allm)
    head = [f"賽事：{zh.tournament(t['name'])}（{zh.level(t['level'])}），當地 {day}"]
    facts: list[str] = []
    if style == "quick":
        facts += sl.champions(daym, t.get('level'))
        per_event = {}
        for s in stories:
            per_event.setdefault(s["event"], s)
        for s in sorted(per_event.values(), key=lambda s: -s["score"]):
            facts += s["facts"]
        tw = sl.taiwan_facts(con, t, allm, whole=True)
        # 每組台灣選手被淘汰的那場（含爆冷標記）；只給名次時模型會自己加「爆冷」（2026-10-01 台北公開賽試寫）
        last_loss = {}
        for m in allm:
            if m["loser"]["home"] and not m["winner"]["home"] and m["round"] in QUICK_TPE_ROUNDS:
                last_loss[(m["event"], m["loser"]["pairing_id"])] = m
        facts += [f for f in tw if "最後名次" in f and re.search(r"冠軍|亞軍|四強|八強", f)]
        facts += sl.taiwan_facts(con, t, list(last_loss.values()), whole=False)
        nxt = _next_tournament(con, t)
        if nxt:
            facts.append(f"下一站：{nxt}")
    elif style in ("story", "daily_story"):
        if not stories or (style == "daily_story" and stories[0]["score"] < DAILY_STORY_MIN):
            return None
        facts += stories[0]["facts"]
    elif style == "taiwan":
        facts += sl.taiwan_facts(con, t, allm, whole=True)
        if not facts:
            return None
    elif style == "daily_taiwan":
        facts += sl.taiwan_facts(con, t, daym, whole=False)
        if not facts:
            return None
        facts += [f"明日看點（台灣時間）：{p}" for p in (next_preview or []) if "中華台北" in p or any(
            n in p for n in [r["name_zh"] for r in zh.load_player_table()])]
    elif style == "numbers":
        kinds, picked = set(), []
        for s in stories:
            if s["kind"] in ("rank_gap", "h2h_dominance", "comeback", "run_summary") and s["kind"] not in kinds:
                kinds.add(s["kind"])
                picked.append(s)
            if len(picked) == 3:
                break
        if len(picked) < 3:
            return None
        for s in picked:
            facts += s["facts"]
        facts += sl.champions(daym, t.get('level'))
    elif style == "daily_quick":
        facts += [sl.match_fact(m) for m in sorted(daym, key=digest._sort_key)]
    dedup = list(dict.fromkeys(head + facts))
    flags = {"upset": any("規則判定" in f for f in dedup), "comeback": any("逆轉" in f for f in dedup),
             "first_title": any("第一座" in f for f in dedup)}
    return {"facts": dedup, "flags": flags, "stories": stories}


def weekly_facts(con, week: str | None = None) -> dict | None:
    """每週排名更新：本週 vs 上週（只用 ranking_snapshot）。"""
    weeks = [w for (w,) in con.execute("SELECT DISTINCT week_date FROM ranking_snapshot ORDER BY 1 DESC LIMIT 2")] \
        if week is None else [week] + [w for (w,) in con.execute(
            "SELECT MAX(week_date) FROM ranking_snapshot WHERE week_date < ?", (week,))]
    if len(weeks) < 2 or not weeks[1]:
        return None
    now, prev = weeks
    tracked = {int(r["player_id"]) for r in zh.load_player_table() if r.get("track") == "Y"}
    facts = [f"世界排名 {now} 更新（與 {prev} 比較）"]
    for ev in ["MS", "WS", "MD", "WD", "XD"]:
        cur = {pid: (r, pts) for pid, r, pts in con.execute(
            "SELECT pairing_id, rank, points FROM ranking_snapshot WHERE week_date=? AND event=?", (now, ev))}
        old = {pid: r for pid, r in con.execute(
            "SELECT pairing_id, rank FROM ranking_snapshot WHERE week_date=? AND event=?", (prev, ev))}
        name = lambda pid: digest._side(con, pid)["name"]
        country = lambda pid: zh.country(digest._side(con, pid)["country"])
        evz = zh.EVENT[ev]
        for pid, (r, pts) in sorted(cur.items(), key=lambda kv: kv[1][0]):
            members = {a for row in con.execute("SELECT player_a_id, player_b_id FROM pairing WHERE pairing_id=?", (pid,))
                       for a in row if a}
            if members & tracked and r <= 100:
                o = old.get(pid)
                change = "新進榜" if o is None else ("持平" if o == r else (f"上升 {o - r} 名" if o > r else f"下滑 {r - o} 名"))
                facts.append(f"台灣 {evz} {name(pid)}：第 {r} 名（上週 {o if o else '—'}，{change}）")
        top_now = {pid for pid, (r, _) in cur.items() if r <= 10}
        top_old = {pid for pid, r in old.items() if r <= 10}
        for pid in top_now - top_old:
            facts.append(f"{evz} 前 10 名新進：{name(pid)}（{country(pid)}），第 {cur[pid][0]} 名（上週 {old.get(pid, '—')}）")
        for pid in top_old - top_now:
            facts.append(f"{evz} 跌出前 10：{name(pid)}（{country(pid)}），第 {cur[pid][0] if pid in cur else '百名外'} 名（上週 {old[pid]}）")
        risers = sorted(((old[pid] - r, pid, r) for pid, (r, _) in cur.items() if r <= 50 and pid in old and old[pid] > r),
                        reverse=True)[:1]
        for up, pid, r in risers:
            facts.append(f"{evz} 前 50 名內升最多：{name(pid)}（{country(pid)}），上升 {up} 名到第 {r} 名")
    return {"facts": facts, "flags": {"upset": False, "comeback": False, "first_title": False}, "week": now}


# ---------------------------------------------------------------- 產生與檢查
def check(out: dict, facts: list[str], flags: dict) -> list[str]:
    """回傳不通過的原因（空 = 通過）。"""
    problems = []
    titles = out.get("titles") or []
    segs = out.get("segments") or []
    if len(titles) != 3 or not segs:
        return ["格式不對：要 3 個標題與至少一段"]
    voice = "".join(s.get("voice", "") for s in segs)
    text = "\n".join(titles + [s.get("voice", "") + " " + s.get("card", "") for s in segs])
    source = "\n".join(facts)
    missing = unverified(text, source)
    if missing:
        problems.append("事實清單查不到：" + "、".join(missing[:8]))
    bad = unlicensed_upsets(text, source)
    if bad:
        problems.append(f"「爆冷」用在規則沒判定的場次：{bad[0][:60]}")
    if "逆轉" in text and not flags["comeback"]:
        problems.append("「逆轉」沒有對應的故事")
    if re.search(r"首冠|首座", text) and not flags["first_title"]:
        problems.append("「首冠／首座」沒有對應的故事")
    if third_place_word(text):
        problems.append("不要用「季軍」「第三名」，四強輸球改寫成銅牌或四強（notes 23:15）")
    repeated = rehashed_scores(voice)
    if repeated:
        problems.append("同一個比分換角度重講：" + "、".join(repeated[:3]))
    n = len(re.sub(r"\s", "", voice))
    if not VOICE_RANGE[0] <= n <= VOICE_RANGE[1]:
        problems.append(f"口播 {n} 字，不在 {VOICE_RANGE[0]}–{VOICE_RANGE[1]}")
    return problems


def rehashed_scores(voice: str) -> list[str]:
    """口播裡同一組比分正反兩個方向都出現（「21 比 11」又「11 比 21」）＝換角度重講（notes 15:40 反例：9/28 單一故事）。"""
    from brief.llm import SCORE
    pairs = {(int(a), int(b)) for a, b in SCORE.findall(voice)}
    return sorted({f"{max(p)}-{min(p)}" for p in pairs if p[0] != p[1] and (p[1], p[0]) in pairs})


def _parse(raw: str) -> dict:
    try:
        return json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
    except ValueError:
        return {}


def make_script_llm(purpose: str, con, effort: str | None = None, model: str | None = None, max_tokens: int | None = None):
    from brief import llm as llm_mod
    return llm_mod.AnthropicLLM(purpose, con=con, effort=effort or SCRIPT_EFFORT[purpose],
                                max_tokens=max_tokens or SCRIPT_MAX_TOKENS, model=model)


def daily_budget() -> float:
    try:
        return float(_env("LLM_DAILY_BUDGET_USD") or DEFAULT_DAILY_BUDGET)
    except ValueError:
        return DEFAULT_DAILY_BUDGET


def taipei_today() -> str:
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=8)).date().isoformat()


def over_budget(con, today: str | None = None) -> bool:
    """台北時間今天的 LLM 總費用（日報＋腳本）超過預算 → 腳本一律跳過（notes 15:20）。"""
    from brief.llm import spent_taipei_day
    return con is not None and spent_taipei_day(con, today or taipei_today()) > daily_budget()


def budget_alert(con, alert, today: str, who: str = "腳本") -> None:
    """超過預算的告警：每個工作（腳本、粉專貼文）每天各發一次。"""
    if con is None or alert is None:
        return
    con.execute("CREATE TABLE IF NOT EXISTS budget_alerts (day TEXT NOT NULL, who TEXT NOT NULL, PRIMARY KEY (day, who))")
    if con.execute("INSERT OR IGNORE INTO budget_alerts (day, who) VALUES (?, ?)", (today, who)).rowcount:
        con.commit()
        from brief.llm import spent_taipei_day
        alert(f"**LLM 每日預算**：台北 {today} 已花 US${spent_taipei_day(con, today):.2f}，超過 US${daily_budget():.2f}，"
              f"今天的{who}跳過（日報、今日重點照跑）")


def log_check(con, ctx: dict | None, style: str, llm, attempt: int, problems: list[str]) -> None:
    if con is None:
        return
    con.executescript(CHECK_TABLE)
    ctx = ctx or {}
    con.execute("INSERT INTO script_check (tournament_id, local_date, style, model, effort, attempt, passed, problems) "
                "VALUES (?,?,?,?,?,?,?,?)", (ctx.get("tournament_id"), ctx.get("day"), style, getattr(llm, "model", None),
                                             getattr(llm, "effort", None), attempt, int(not problems), "；".join(problems) or None))
    con.commit()


def generate(llm, style: str, facts: list[str], flags: dict, con=None, ctx: dict | None = None) -> tuple[dict | None, list[str]]:
    label, example, _, _ = STYLES[style]
    ex = (EXAMPLES / example).read_text(encoding="utf-8") if example else "（這種風格沒有範例，照說明寫）"
    user = (f"風格：{label}。{STYLE_GUIDE[style]}\n\n範例（只看格式與語氣）：\n{ex}\n\n事實清單：\n"
            + "\n".join(f"{i + 1}. {f}" for i, f in enumerate(facts)))
    if hasattr(llm, "task"):
        llm.task = f"script_{style}"
    problems: list[str] = []
    for attempt in range(2):
        prompt = user if not problems else user + "\n\n上一版沒有通過檢查，請修正：" + "；".join(problems)
        out = _parse(llm.complete(SYSTEM, prompt))
        problems = check(out, facts, flags)
        log_check(con, ctx, style, llm, attempt + 1, problems)
        if not problems:
            return out, []
    return None, problems


def to_markdown(label: str, out: dict) -> str:
    lines = [f"### {label}", "", "標題："] + [f"{i + 1}. {t}" for i, t in enumerate(out["titles"])]
    lines += ["", "| 秒數 | 口播 | 字卡／畫面 |", "|---|---|---|"]
    lines += [f"| {s.get('time', '')} | {s.get('voice', '')} | {s.get('card', '')} |" for s in out["segments"]]
    return "\n".join(lines)


def to_discord(label: str, title: str, out: dict) -> str:
    lines = [f"🎬 **影片腳本草稿｜{title}｜{label}**"] + [f"{i + 1}. {t}" for i, t in enumerate(out["titles"])]
    lines += [f"`{s.get('time', '')}` {s.get('voice', '')} ／ 字卡：{s.get('card', '')}" for s in out["segments"]]
    return "\n".join(lines)


def styles_for_day(t: dict, day: str, deepest_round: str | None) -> list[str]:
    """交接單 005：所有比賽日（含決賽日）都走故事引擎，每天 1–2 支。舊的四種風格只在 --styles 指定時產生（對照用）。"""
    return ["story_main"]


def flag_for(style: str, t: dict, day: str) -> str:
    if style == "story_main":
        return "SCRIPT_FINAL" if day == t["end_date"] else "SCRIPT_DAILY"
    return STYLES[style][3]


def run_for_day(con, t: dict, day: str, deepest_round: str | None = None, next_preview: list[str] | None = None,
                make_llm=None, send=None, alert=None, styles: list[str] | None = None) -> dict:
    """產生一站一天的所有風格，寫檔；開關打開才推 Discord。回傳 {style: 結果}。"""
    from brief import llm as llm_mod
    styles = styles or styles_for_day(t, day, deepest_round)
    send = send if send is not None else script_sender()
    title = f"{zh.tournament(t['name'])}｜{day}"
    results, md = {}, [f"# 影片腳本草稿｜{title}", ""]
    today = taipei_today()
    for style in styles:
        if over_budget(con, today):
            results[style] = {"status": "skipped_budget"}
            budget_alert(con, alert, today)
            continue
        label, _, purpose, flag = STYLES[style]
        if style == "story_main":
            res, part = _story_main(con, t, day, title, make_llm, send, alert)
            results[style] = res
            md += part
            continue
        f = facts_for(con, t, day, style, next_preview)
        if f is None:
            results[style] = {"status": "skipped"}
            continue
        model = make_llm(purpose) if make_llm else make_script_llm(purpose, con)
        out, problems = generate(model, style, f["facts"], f["flags"], con, {"tournament_id": t["tournament_id"], "day": day})
        if out is None:
            results[style] = {"status": "failed", "problems": problems}
            md += [f"### {label}", "", f"（未通過事實檢查：{'；'.join(problems)}）", ""]
            if alert:
                alert(f"**腳本產生失敗**｜{title}｜{label}：{'；'.join(problems)[:300]}")
            continue
        results[style] = {"status": "ok", "out": out, "facts": f["facts"]}
        md += [to_markdown(label, out), ""]
        if send and _env(flag) == "1":
            send(to_discord(label, title, out))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{day}_{t['tournament_id']}.md"
    path.write_text("\n".join(md), encoding="utf-8")
    return {"path": str(path), "styles": results}


def _story_main(con, t: dict, day: str, title: str, make_llm, send, alert) -> tuple[dict, list[str]]:
    """故事引擎：選 1–2 個故事，各寫一支。回傳（結果, markdown 段落）。"""
    from brief import story
    cands, daym, allm = story.day_candidates(con, t, day)
    picks = story.pick(cands)
    if not picks:
        return {"status": "skipped", "reason": f"沒有分數 ≥ {story.STORY_MIN} 的故事"}, \
               [f"（今天沒有分數 ≥ {story.STORY_MIN} 的故事，不產生）", ""]
    flag = flag_for("story_main", t, day)
    md, scripts = [], []
    for i, c in enumerate(picks, 1):
        facts = story.materials(con, t, day, c, cands, daym, allm)
        model = make_llm("heavy") if make_llm else make_script_llm("heavy", con, effort=story.STORY_EFFORT)
        editor = make_llm("editor") if make_llm else make_script_llm("heavy", con, effort="low")     # 編輯：Opus low（10-02 05:55）
        out, problems = story.generate(model, facts, con, {"tournament_id": t["tournament_id"], "day": day}, editor=editor)
        head = f"故事 {i}｜{c['kind']}"
        if out is None:
            scripts.append({"status": "failed", "kind": c["kind"], "score": c["final"], "problems": problems})
            md += [f"### {head}", "", f"（未通過事實檢查：{'；'.join(problems)}）", ""]
            if alert:
                alert(f"**腳本產生失敗**｜{title}｜{head}：{'；'.join(problems)[:300]}")
            continue
        scripts.append({"status": "ok", "kind": c["kind"], "score": c["final"], "out": out, "facts": facts})
        md += [story.to_markdown(head, c, out), ""]
        if send and _env(flag) == "1":
            send(story.to_discord(f"{title}｜{head}", out))
    ok = any(x["status"] == "ok" for x in scripts)
    return {"status": "ok" if ok else "failed", "scripts": scripts}, md


def run_weekly(con, make_llm=None, send=None, alert=None, week: str | None = None) -> dict:
    from brief import llm as llm_mod
    send = send if send is not None else script_sender()
    f = weekly_facts(con, week)
    if f is None:
        return {"status": "skipped"}
    today = taipei_today()
    if over_budget(con, today):
        budget_alert(con, alert, today)
        return {"status": "skipped_budget"}
    model = make_llm("heavy") if make_llm else make_script_llm("heavy", con)
    out, problems = generate(model, "weekly_rank", f["facts"], f["flags"], con, {"day": f["week"]})
    title = f"世界排名 {f['week']}"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{f['week']}_ranking.md"
    if out is None:
        path.write_text(f"# 影片腳本草稿｜{title}\n\n（未通過事實檢查：{'；'.join(problems)}）\n", encoding="utf-8")
        if alert:
            alert(f"**腳本產生失敗**｜{title}：{'；'.join(problems)[:300]}")
        return {"status": "failed", "problems": problems, "path": str(path)}
    path.write_text(f"# 影片腳本草稿｜{title}\n\n" + to_markdown("排名更新", out) + "\n", encoding="utf-8")
    if send and _env("SCRIPT_WEEKLY") == "1":
        send(to_discord("排名更新", title, out))
    return {"status": "ok", "out": out, "facts": f["facts"], "path": str(path)}


def enabled(flag: str) -> bool:
    return _env(flag) in ("1", "dry")


def wanted_styles(t: dict, day: str, deepest_round: str | None) -> list[str]:
    """開關有開的風格（決賽日看 SCRIPT_FINAL，其餘看 SCRIPT_DAILY）。"""
    return [s for s in styles_for_day(t, day, deepest_round) if enabled(flag_for(s, t, day))]


def script_sender():
    """腳本專用頻道（DISCORD_WEBHOOK_SCRIPTS）；沒設定就回傳 None（只寫檔）。"""
    from brief import discord
    url = _env("DISCORD_WEBHOOK_SCRIPTS")
    return (lambda text: discord.send(url, text)) if url else None


def cost_since(con, since: str, until: str | None = None) -> float:
    """llm_call.called_at 是 UTC（SQLite datetime('now')）。"""
    try:
        return con.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM llm_call WHERE task LIKE 'script%' AND called_at >= ? "
                           "AND called_at < ?", (since, until or "9999")).fetchone()[0]
    except Exception:  # noqa: BLE001
        return 0.0


def main():
    from brief.crawler import connect
    ap = argparse.ArgumentParser(description="口播腳本（只寫檔；推送看 .env 開關）")
    ap.add_argument("--db", default="brief.db")
    ap.add_argument("--tournament", type=int)
    ap.add_argument("--date")
    ap.add_argument("--styles", nargs="*")
    ap.add_argument("--weekly", action="store_true")
    a = ap.parse_args()
    con = connect(a.db)
    zh.apply_player_names(con)
    if a.weekly:
        res = run_weekly(con, week=a.date)
        print(res.get("path"), res["status"], res.get("problems", ""))
        return
    row = con.execute("SELECT tournament_id, name, level, start_date, end_date FROM tournament WHERE tournament_id=?",
                      (a.tournament,)).fetchone()
    t = dict(zip(["tournament_id", "name", "level", "start_date", "end_date"], row))
    allm = sl.tournament_matches(con, a.tournament, a.date)
    deepest = max((m["round"] for m in allm if m["date"] == a.date),
                  key=lambda r: digest.ROUND_ORDER.index(r) if r in digest.ROUND_ORDER else -1, default=None)
    res = run_for_day(con, t, a.date, deepest, styles=a.styles)
    print(res["path"])
    for k, v in res["styles"].items():
        print(k, v["status"], v.get("problems", ""))


if __name__ == "__main__":
    main()
