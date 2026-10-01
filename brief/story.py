"""故事引擎（交接單 005）：每支腳本一個故事當主軸，每天 1–2 支依故事分數挑。

結構（所有腳本共用）：開場 0–4s → 故事（主體）→ 冷知識 約 10s → 賽果背景 ≤ 15s（可省略）→ 互動 約 6s；
全長 30–60 秒，素材不夠就短。原本的四種風格不再各產一份：快報降為「賽果背景」、台灣視角變成選題加分、
數據型併入冷知識。

素材：故事候選（`storylines.story_candidates`，全部可回資料庫查）＋冷知識（同一組選手的其他候選、整站紀錄、
種子）＋賽果背景（台灣選手、決賽日冠軍）＋新聞裡的爭議句（`foreign_article` 內文，附出處）。
推測：AI 可以寫資料庫以外的背景，但每句標「⚠️推測」、最後附待查清單；推測句不進事實檢查。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from brief import storylines as sl, zh
from brief.llm import SENTENCE

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "tests" / "fixtures" / "story_examples"
STORY_MIN = 9.0          # 分數門檻（status.md 記錄；依 Raymond 回饋調）
TPE_BONUS = 3.0          # 有台灣選手的故事加分（台灣視角變成選題偏好）
MAX_PER_DAY = 2
SPEC = "⚠️推測"
CONTROVERSY = re.compile(r"裁判|挑戰|爭議|換球|抗議|判決|黃牌|紅牌|鷹眼|發球違例|申訴")

SYSTEM = """你是羽球短影音的腳本作者，觀眾是台灣的羽球愛好者，審稿人是前職業選手 Raymond。
用繁體中文、台灣用語寫一支口播腳本，**以一個故事當主軸**，賽果只當背景。
結構：開場 0–4s（故事最強的一句，含一個數字或反差）→ 故事（主體，一個故事講透）→ 冷知識 約 10s（數據紀錄、選手背景、規則與賽制擇一）
→ 賽果背景 ≤ 15s（只帶到跟故事有關、或台灣選手的結果；可省略）→ 互動 約 6s（一個問句）。
全長 30–60 秒：素材夠就約 60 秒（口播 240–280 字），不夠就寫短（120–200 字）。
硬性規則：
- 名字、國家、排名、比分、交手紀錄、日期、名次只能用「事實清單」的內容，照抄不推測；名字照事實清單的寫法，同一人只能一種寫法，絕對不要自己翻譯或音譯
- 比分照事實清單（勝方在前）；主詞是敗方時倒過來寫成主詞的角度；**不准換角度重講同一個比分湊秒數**
- 「爆冷／冷門」只能用在事實清單標了「規則判定爆冷」的場次；「逆轉」只能用在事實清單寫到逆轉的場次；「首冠」只能用在寫到「第一座」的選手
- 只有事實清單寫到金牌／銀牌／銅牌的賽事才能用獎牌字眼；World Tour 等賽事寫冠軍、亞軍、四強
- 事實清單以外的背景（年齡、經歷、規則解釋、原因推論）可以寫，但**每一句都要在句尾加「⚠️推測」**，語氣用「可能、大約、據說」，不能寫成確定語氣；
  而且每一句推測都要列進 todo（推測內容、依據、建議怎麼查）。數字若不在事實清單，那句就必須是推測句
- 日期用事實清單的寫法或相對「今天」換算（例如「前天」），不要自己編日期
- 範例只示範結構與語氣，範例裡的名字與數字不能用
只輸出 JSON：{"titles": ["…", "…", "…"], "segments": [{"time": "0–4s", "part": "開場|故事|冷知識|賽果背景|互動", "voice": "口播", "card": "字卡建議"}],
"todo": [{"claim": "推測內容", "basis": "依據", "how": "建議怎麼查"}]}"""


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


def materials(con, t: dict, day: str, c: dict, cands: list[dict], daym: list[dict]) -> list[str]:
    head = [f"賽事：{zh.tournament(t['name'])}（{zh.level(t['level'])}），今天是當地 {day}"]
    names = _names(c, daym)
    trivia = [f for o in cands if o is not c and o["facts"][0] != c["facts"][0] and any(n in o["facts"][0] for n in names)
              for f in o["facts"][1:]][:4]
    if day == t["end_date"]:
        trivia += [f for o in cands if o["kind"] == "record" and o is not c for f in o["facts"]][:4]   # 紀錄連同那一場
    back = sl.champions(daym, t.get("level")) if day == t["end_date"] else []
    back += [sl.match_fact(m) for m in daym if (m["winner"]["home"] or m["loser"]["home"])][:3]
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


def generate(llm, facts: list[str], con=None, ctx: dict | None = None) -> tuple[dict | None, list[str]]:
    from brief import script
    flags = _flags(facts)
    user = (f"範例（只看結構與語氣）：\n{_examples()}\n\n事實清單：\n" + "\n".join(f"{i + 1}. {f}" for i, f in enumerate(facts)))
    if hasattr(llm, "task"):
        llm.task = "script_story_main"
    problems: list[str] = []
    for attempt in range(2):
        prompt = user if not problems else user + "\n\n上一版沒有通過檢查，請修正：" + "；".join(problems)
        raw = llm.complete(SYSTEM, prompt)
        try:
            out = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
        except ValueError:
            out = {}
        problems = check(out, facts, flags)
        script.log_check(con, ctx, "story_main", llm, attempt + 1, problems)
        if not problems:
            return out, []
    return None, problems


def to_markdown(title: str, c: dict, out: dict) -> str:
    lines = [f"### {title}", "", f"故事：{c['kind']}（分數 {c['final']:.1f}）", "", "標題："]
    lines += [f"{i + 1}. {x}" for i, x in enumerate(out["titles"])]
    lines += ["", "| 秒數 | 段落 | 口播 | 字卡建議 |", "|---|---|---|---|"]
    lines += [f"| {s.get('time', '')} | {s.get('part', '')} | {s.get('voice', '')} | {s.get('card', '')} |" for s in out["segments"]]
    if out.get("todo"):
        lines += ["", "待查清單："] + [f"- {x.get('claim', '')}（依據：{x.get('basis', '')}；怎麼查：{x.get('how', '')}）" for x in out["todo"]]
    return "\n".join(lines)


def to_discord(title: str, out: dict) -> str:
    lines = [f"🎬 **影片腳本草稿｜{title}**"] + [f"{i + 1}. {x}" for i, x in enumerate(out["titles"])]
    lines += [f"`{s.get('time', '')}` {s.get('voice', '')} ／ 字卡：{s.get('card', '')}" for s in out["segments"]]
    if out.get("todo"):
        lines += ["待查：" + "；".join(x.get("claim", "") for x in out["todo"])]
    return "\n".join(lines)
