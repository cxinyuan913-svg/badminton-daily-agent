"""故事引擎（交接單 005）：每支腳本一個故事當主軸，每天 1–2 支依故事分數挑。

結構（所有腳本共用）：開場 0–4s → 故事（主體）→ 冷知識 約 10s → 賽果背景 ≤ 15s（可省略）→ 互動 約 6s；
全長 30–60 秒，素材不夠就短。原本的四種風格不再各產一份：快報降為「賽果背景」、台灣視角變成選題加分、
數據型併入冷知識。

素材：故事候選（`storylines.story_candidates`，全部可回資料庫查）＋冷知識（同一組選手的其他候選、整站紀錄、
種子）＋賽果背景（故事主角這站的其他場次；準則 R3：不放台灣選手或冠軍名單，台灣戰報交給粉專）＋新聞裡的爭議句。
推測：AI 可以寫資料庫以外的背景，但每句標「⚠️推測」、最後附待查清單；推測句不進事實檢查。
編輯檢查（notes 20:35）：事實檢查通過後，Sonnet low 逐條對照 docs/video/review-guidelines.md 打分；有不通過就把意見
交回寫手重寫一次，第二次仍不過就在腳本最後列「編輯意見」，照樣產出給 Raymond 看。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from brief import storylines as sl, zh
from brief.llm import SENTENCE

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "tests" / "fixtures" / "story_examples"
STORY_EFFORT = "low"     # 推理強度實驗（notes 15:20）：opus low 兩個故事都一次通過、每支約 US$0.05；high 在 4000 token 內會被截斷
STORY_MIN = 9.0          # 分數門檻（status.md 記錄；依 Raymond 回饋調）
TPE_BONUS = 3.0          # 有台灣選手的故事加分（台灣視角變成選題偏好）
MAX_PER_DAY = 2
SPEC = "⚠️推測"
CONTROVERSY = re.compile(r"裁判|挑戰|爭議|換球|抗議|判決|黃牌|紅牌|鷹眼|發球違例|申訴")

GUIDELINES = ROOT / "docs" / "video" / "review-guidelines.md"   # Raymond 的審稿準則（R1、R2…），每次執行讀最新版

SYSTEM_BASE = """你是羽球短影音的腳本作者，觀眾是台灣的羽球愛好者，審稿人是前職業選手 Raymond。
用繁體中文、台灣用語寫一支口播腳本，**以一個故事當主軸**。
結構：開場 0–4s（故事最強的一句，含一個數字或反差）→ 故事（主體，一個故事講透）→ 冷知識 約 10s（數據紀錄、選手背景、規則與賽制擇一）
→ 賽果背景 ≤ 15s（只放跟這個故事同一批人或同一場比賽的結果；沒有就省略）→ 互動 約 6s（一個問句）。
全長 30–60 秒：素材夠就約 60 秒（口播 240–280 字），不夠就寫短（120–200 字）。
**口播總字數（不含空白，標點算字）硬上限 300 字，超過會被退回**；素材很多時挑最強的 2–3 個事實講，不用全部講完。
字數預算：開場 ≤ 25、故事 ≤ 150、冷知識 ≤ 50、賽果背景 ≤ 50（可省略）、互動 ≤ 25。
資料規則（事實檢查會擋）：
- 名字、國家、排名、比分、交手紀錄、日期、名次只能用「事實清單」的內容，照抄；名字照事實清單的寫法
- 比分照事實清單（勝方在前）；主詞是敗方時倒過來寫成主詞的角度
- 「爆冷／冷門」只能用在事實清單標了「規則判定爆冷」的場次；「逆轉」只能用在事實清單寫到逆轉的場次；「首冠」只能用在寫到「第一座」的選手
- 事實清單以外的背景（年齡、經歷、規則解釋、原因推論）每一句句尾加「⚠️推測」，語氣用「可能、大約、據說」，並列進 todo（推測內容、依據、建議怎麼查）。數字若不在事實清單，那句就必須是推測句
- 不要自己算出事實清單沒有的新數字（例如交手 8 次就說「第 9 次碰面」、相加或相減出來的數字）
- 日期用事實清單的寫法或相對「今天」換算，不要自己編日期
- 範例只示範結構與語氣，範例裡的名字與數字不能用
- 事實清單的「官方排名走勢」「平均每局分差」「退賽次數」只是數據，不是原因：用它說明轉折時要寫成「數據顯示…」，推論原因就標推測
只輸出 JSON：{"titles": ["…", "…", "…"], "segments": [{"time": "0–4s", "part": "開場|故事|冷知識|賽果背景|互動", "voice": "口播", "card": "字卡建議"}],
"todo": [{"claim": "推測內容", "basis": "依據", "how": "建議怎麼查"}]}"""


def guidelines() -> str:
    """docs/video/review-guidelines.md 全文（claude.ai 會持續加 R4、R5…；每次讀檔，不快取）。"""
    try:
        return GUIDELINES.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def guideline_ids(text: str | None = None) -> list[str]:
    return re.findall(r"^## (R\d+)", guidelines() if text is None else text, re.M)


def system_prompt() -> str:
    g = guidelines()
    return SYSTEM_BASE + ("\n\n以下是 Raymond 的審稿準則，每一條都要遵守：\n" + g if g else "")


EDITOR_SYSTEM = """你是羽球短影音的編輯，替前職業選手 Raymond 先審一遍稿。只看「審稿準則」裡編號 R 開頭的每一條，逐條判斷這支腳本有沒有違反。
- 通過就寫 pass: true；不通過寫 pass: false，quote 引用有問題的原句（照抄），comment 用一句話說怎麼改
- 只依準則判斷，不要挑準則以外的毛病；不確定就算通過
只輸出 JSON：{"items": [{"rule": "R1", "pass": true, "quote": "", "comment": ""}]}"""


# ---------------------------------------------------------------- 選題
def _tpe(c: dict) -> bool:
    return "中華台北" in c["facts"][0]


def pick(cands: list[dict], threshold: float = STORY_MIN) -> list[dict]:
    """分數最高的 1 支；最高分的不含台灣選手、而有含台灣選手且分數 ≥ 門檻的故事，再加 1 支。
    分數 = 候選分數 ＋ 台灣加分；低於門檻就不產（寧缺勿濫）。"""
    scored = sorted(({**c, "final": c["score"] + (TPE_BONUS if _tpe(c) else 0)} for c in cands), key=lambda c: -c["final"])
    scored = [c for c in scored if c["final"] >= threshold]
    if not scored:
        return []
    out = [scored[0]]
    if not _tpe(scored[0]):
        tpe = next((c for c in scored[1:] if _tpe(c)), None)
        if tpe:
            out.append(tpe)
    return out[:MAX_PER_DAY]


def day_candidates(con, t: dict, day: str) -> tuple[list[dict], list[dict], list[dict]]:
    allm = sl.tournament_matches(con, t["tournament_id"], day)
    daym = [m for m in allm if m["date"] == day]
    cands = sl.story_candidates(con, t, daym, allm, with_records=(day == t["end_date"]))
    return cands, daym, allm


# ---------------------------------------------------------------- 素材
def _names(c: dict, daym: list[dict]) -> set[str]:
    first = c["facts"][0]
    return {s["name"] for m in daym for s in (m["winner"], m["loser"]) if s["name"] in first}


def news_controversy(con, names: set[str]) -> list[str]:
    """已收新聞內文裡，同一句提到選手且有爭議關鍵字的句子（附出處）；找不到就沒有。"""
    out = []
    keys = {n for n in names} | {w for n in names for w in re.findall(r"[A-Z]{2,}", n)}
    try:
        rows = con.execute("SELECT source, url, text FROM foreign_article").fetchall()
    except Exception:  # noqa: BLE001
        return []
    for source, url, text in rows:
        for sent in re.split(r"[。！？\n]", text or ""):
            if CONTROVERSY.search(sent) and any(k and k in sent for k in keys) and 8 <= len(sent) <= 120:
                out.append(f"新聞（{source}，{url}）：{sent.strip()}")
    return out[:2]


def seed_facts(con, c: dict, daym: list[dict]) -> list[str]:
    """種子序號（match.side*_seed）：卡關、宰制故事常用來解釋為什麼老在同一輪碰到。"""
    first = c["facts"][0]
    m = next((x for x in daym if sl.match_fact(x) == first), None)
    if m is None:
        return []
    row = con.execute("SELECT side1_id, side1_seed, side2_seed FROM match WHERE match_id=?", (m["match_id"],)).fetchone()
    if not row:
        return []
    s1, seed1, seed2 = row
    out = []
    for side in (m["winner"], m["loser"]):
        seed = seed1 if side["pairing_id"] == s1 else seed2
        if seed:
            out.append(f"這站{side['name']}是第 {str(seed).strip()} 種子")
    return out


def materials(con, t: dict, day: str, c: dict, cands: list[dict], daym: list[dict], allm: list[dict] | None = None) -> list[str]:
    head = [f"賽事：{zh.tournament(t['name'])}（{zh.level(t['level'])}），今天是當地 {day}"]
    names = _names(c, daym)
    trivia = [f for o in cands if o is not c and o["facts"][0] != c["facts"][0] and any(n in o["facts"][0] for n in names)
              for f in o["facts"][1:]][:4]
    if day == t["end_date"]:
        trivia += [f for o in cands if o["kind"] == "record" and o is not c for f in o["facts"]][:4]   # 紀錄連同那一場
    # 準則 R3：賽果背景只放故事主角這站的其他場次（不放台灣選手、不放冠軍名單）
    back = [sl.match_fact(m) for m in (allm or daym) if sl.match_fact(m) != c["facts"][0]
            and any(n in (m["winner"]["name"], m["loser"]["name"]) for n in names)][-3:]
    # 同一場的其他候選（例：完全宰制＋卡在同一輪）併進同一個故事
    same = [f for o in cands if o is not c and o["facts"][0] == c["facts"][0] for f in o["facts"][1:]]
    facts = head + ["【故事】"] + c["facts"] + same + seed_facts(con, c, daym)
    facts += (["【冷知識素材】"] + trivia) if trivia else []
    facts += (["【賽果背景素材】"] + back) if back else []
    facts += (["【新聞】"] + news) if (news := news_controversy(con, names)) else []
    return list(dict.fromkeys(facts))


# ---------------------------------------------------------------- 檢查
def strip_speculation(text: str) -> str:
    """推測句（帶 ⚠️推測）不進事實檢查。"""
    return "".join(s for s in re.findall(r"[^。；;！!？?\n]+[。；;！!？?\n]?", text) if SPEC not in s)


def check(out: dict, facts: list[str], flags: dict) -> list[str]:
    from brief import script
    segs = out.get("segments") or []
    if SPEC in "".join(s.get("voice", "") + s.get("card", "") for s in segs) and not out.get("todo"):
        return ["有推測句但沒有待查清單"]
    checked = {**out, "titles": [strip_speculation(x) for x in out.get("titles") or []],
               "segments": [{**s, "voice": strip_speculation(s.get("voice", "")), "card": strip_speculation(s.get("card", ""))}
                            for s in segs]}
    problems = script.check(checked, facts, flags)
    # 長度以完整口播（含推測句）計
    full = "".join(s.get("voice", "") for s in segs)
    n = len(re.sub(r"\s", "", full))
    problems = [p for p in problems if not p.startswith("口播 ")]
    if not script.VOICE_RANGE[0] <= n <= script.VOICE_RANGE[1]:
        problems.append(f"口播 {n} 字，不在 {script.VOICE_RANGE[0]}–{script.VOICE_RANGE[1]}")
    return problems


def _flags(facts: list[str]) -> dict:
    return {"upset": any("規則判定" in f for f in facts), "comeback": any("逆轉" in f for f in facts),
            "first_title": any("第一座" in f for f in facts)}


def _examples() -> str:
    return "\n\n".join(p.read_text(encoding="utf-8") for p in sorted(EXAMPLES.glob("*.md")))


def _json(raw: str) -> dict:
    try:
        return json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
    except ValueError:
        return {}


def write(llm, user: str, facts: list[str], con=None, ctx: dict | None = None, attempts: int = 2) -> tuple[dict | None, list[str]]:
    """寫手：最多 attempts 次，直到事實檢查通過。"""
    from brief import script
    flags = _flags(facts)
    if hasattr(llm, "task"):
        llm.task = "script_story_main"
    problems: list[str] = []
    for attempt in range(attempts):
        prompt = user if not problems else user + "\n\n上一版沒有通過檢查，請修正：" + "；".join(problems)
        out = _json(llm.complete(system_prompt(), prompt))
        problems = check(out, facts, flags)
        script.log_check(con, ctx, "story_main", llm, attempt + 1, problems)
        if not problems:
            return out, []
    return None, problems


def script_text(out: dict) -> str:
    lines = ["標題：" + "／".join(out.get("titles") or [])]
    lines += [f"[{s.get('time', '')}｜{s.get('part', '')}] {s.get('voice', '')}（字卡：{s.get('card', '')}）" for s in out.get("segments") or []]
    return "\n".join(lines)


def edit(editor, out: dict, con=None, ctx: dict | None = None) -> list[dict]:
    """編輯檢查（LLM-as-judge）：回傳不通過的條目 [{rule, quote, comment}]；編輯本身出錯時當作通過（不擋稿）。"""
    from brief import script
    if editor is None:
        return []
    if hasattr(editor, "task"):
        editor.task = "script_editor"
    g = guidelines()
    ids = guideline_ids(g)
    try:
        res = _json(editor.complete(EDITOR_SYSTEM, f"審稿準則：\n{g}\n\n腳本：\n{script_text(out)}"))
    except Exception:  # noqa: BLE001
        return []
    bad = [x for x in res.get("items") or [] if x.get("pass") is False and x.get("rule") in ids]
    script.log_check(con, ctx, "story_editor", editor, 1, [f"{x['rule']}：{x.get('quote', '')}" for x in bad])
    return bad


def generate(llm, facts: list[str], con=None, ctx: dict | None = None, editor=None) -> tuple[dict | None, list[str]]:
    """寫 → 事實檢查 → 編輯檢查；編輯不過就帶意見重寫一次（重寫也要過事實檢查），仍不過就附「編輯意見」照樣產出。"""
    user = (f"範例（只看結構與語氣）：\n{_examples()}\n\n事實清單：\n" + "\n".join(f"{i + 1}. {f}" for i, f in enumerate(facts)))
    out, problems = write(llm, user, facts, con, ctx)
    if out is None:
        return None, problems
    bad = edit(editor, out, con, ctx)
    out["editor"] = {"first": bad, "rewritten": False, "final": bad}
    if not bad:
        return out, []
    notes = "；".join(f"{x['rule']} 不通過：「{x.get('quote', '')}」→ {x.get('comment', '')}" for x in bad)
    redo, _ = write(llm, user + "\n\n編輯的意見（照著改，事實清單規則照舊）：" + notes, facts, con, ctx, attempts=1)
    if redo is None:                       # 重寫沒過事實檢查：保留第一版，附上編輯意見
        return out, []
    final = edit(editor, redo, con, ctx)
    redo["editor"] = {"first": bad, "rewritten": True, "final": final}
    return redo, []


def to_markdown(title: str, c: dict, out: dict) -> str:
    lines = [f"### {title}", "", f"故事：{c['kind']}（分數 {c['final']:.1f}）", "", "標題："]
    lines += [f"{i + 1}. {x}" for i, x in enumerate(out["titles"])]
    lines += ["", "| 秒數 | 段落 | 口播 | 字卡建議 |", "|---|---|---|---|"]
    lines += [f"| {s.get('time', '')} | {s.get('part', '')} | {s.get('voice', '')} | {s.get('card', '')} |" for s in out["segments"]]
    if out.get("todo"):
        lines += ["", "待查清單："] + [f"- {x.get('claim', '')}（依據：{x.get('basis', '')}；怎麼查：{x.get('how', '')}）" for x in out["todo"]]
    ed = out.get("editor")
    if ed is not None:
        status = "通過" if not ed["final"] else "仍有意見"
        first = "、".join(x["rule"] for x in ed["first"]) or "無"
        lines += ["", f"編輯檢查：{status}（第一次不通過：{first}；{'有' if ed['rewritten'] else '沒有'}重寫）"]
        if ed["final"]:
            lines += ["編輯意見："] + [f"- {x['rule']}：「{x.get('quote', '')}」→ {x.get('comment', '')}" for x in ed["final"]]
    return "\n".join(lines)


def to_discord(title: str, out: dict) -> str:
    lines = [f"🎬 **影片腳本草稿｜{title}**"] + [f"{i + 1}. {x}" for i, x in enumerate(out["titles"])]
    lines += [f"`{s.get('time', '')}` {s.get('voice', '')} ／ 字卡：{s.get('card', '')}" for s in out["segments"]]
    if out.get("todo"):
        lines += ["待查：" + "；".join(x.get("claim", "") for x in out["todo"])]
    return "\n".join(lines)
