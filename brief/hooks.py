"""伏筆（挖坑／填坑）機制（notes 10-02 13:45；審稿準則 R16）。

每篇故事型 FB 貼文與影片腳本埋一個伏筆，之後拉出來寫成獨立一篇，讓觀眾有期待。
  1. 產生時：事實清單除了主線，另給 2–3 條「支線素材」（B1、B2…），每條有懸念方向、答案事實、答案關鍵字。
     寫手挑一條，只寫懸念、不寫答案，輸出 hook: {branch, teaser} → 寫入 story_hook（status=open）。
     程式檢查：正文不可出現答案關鍵字；一篇只能一個伏筆；teaser 要真的在正文裡。編輯也看 R16。
  2. 填坑：沒有新賽果的日子（R6 故事語氣），粉專冷知識優先挑最舊的 open 伏筆寫專題，開頭接回原文。填完 status=filled。
  3. 超過 21 天沒填，或主角之後有新比賽（答案可能過時）→ status=dropped，週一晨報列出。
  4. 週一晨報一行：目前 open 伏筆幾個、各是哪個題目。
"""
from __future__ import annotations

import datetime as dt
import json
import re

TABLE = """
CREATE TABLE IF NOT EXISTS story_hook (
    hook_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    source_kind     TEXT NOT NULL,                -- fb / script
    source_ref      TEXT,                         -- 原貼文日期或腳本檔名
    subject         TEXT,                         -- 主角（pairing／player id，JSON 陣列）
    title           TEXT,                         -- 伏筆題目（週報用）
    teaser          TEXT NOT NULL,                -- 實際寫出去的那句
    answer_facts    TEXT NOT NULL,                -- JSON：填坑要用的事實
    status          TEXT NOT NULL DEFAULT 'open', -- open / filled / dropped
    filled_by       TEXT,
    filled_at       TEXT,
    drop_reason     TEXT
);
"""
EXPIRE_DAYS = 21


def _con(con):
    con.executescript(TABLE)
    return con


def add(con, source_kind: str, source_ref: str, subject: list[int], title: str, teaser: str, answer_facts: list[str]) -> int:
    cur = _con(con).execute(
        "INSERT INTO story_hook (source_kind, source_ref, subject, title, teaser, answer_facts) VALUES (?,?,?,?,?,?)",
        (source_kind, source_ref, json.dumps(subject), title, teaser, json.dumps(answer_facts, ensure_ascii=False)))
    con.commit()
    return cur.lastrowid


def open_hooks(con) -> list[dict]:
    rows = _con(con).execute("""SELECT hook_id, created_at, source_kind, source_ref, subject, title, teaser, answer_facts
                                FROM story_hook WHERE status='open' ORDER BY created_at, hook_id""").fetchall()
    keys = ["hook_id", "created_at", "source_kind", "source_ref", "subject", "title", "teaser", "answer_facts"]
    out = []
    for r in rows:
        d = dict(zip(keys, r))
        d["subject"] = json.loads(d["subject"] or "[]")
        d["answer_facts"] = json.loads(d["answer_facts"])
        out.append(d)
    return out


def fill(con, hook_id: int, filled_by: str) -> None:
    _con(con).execute("UPDATE story_hook SET status='filled', filled_by=?, filled_at=datetime('now') WHERE hook_id=?",
                      (filled_by, hook_id))
    con.commit()


def expire(con, today: str) -> list[dict]:
    """超過 21 天，或主角在伏筆之後有新比賽 → dropped。回傳這次作廢的。"""
    dropped = []
    for h in open_hooks(con):
        age = (dt.date.fromisoformat(today) - dt.date.fromisoformat(h["created_at"][:10])).days
        reason = None
        if age > EXPIRE_DAYS:
            reason = f"超過 {EXPIRE_DAYS} 天沒填"
        elif h["subject"]:
            q = ",".join("?" * len(h["subject"]))
            newer = con.execute(f"""SELECT MIN(match_date) FROM match WHERE (side1_id IN ({q}) OR side2_id IN ({q}))
                                     AND match_date > ?""", (*h["subject"], *h["subject"], h["created_at"][:10])).fetchone()[0]
            if newer:
                reason = f"主角 {newer} 有新比賽，答案可能過時"
        if reason:
            con.execute("UPDATE story_hook SET status='dropped', drop_reason=? WHERE hook_id=?", (reason, h["hook_id"]))
            dropped.append({**h, "drop_reason": reason})
    con.commit()
    return dropped


def weekly_lines(con, today: str) -> list[str]:
    """週一晨報：目前 open 伏筆幾個、各是哪個題目；這週作廢的。"""
    dropped = expire(con, today)
    hooks = open_hooks(con)
    lines = [f"伏筆：open {len(hooks)} 個" + ("：" + "、".join(f"#{h['hook_id']} {h['title']}" for h in hooks) if hooks else "")]
    if dropped:
        lines.append("作廢的伏筆：" + "、".join(f"#{h['hook_id']} {h['title']}（{h['drop_reason']}）" for h in dropped))
    return lines


# ---------------------------------------------------------------- 寫手用
PROMPT = ("伏筆（準則 R16）：從下面的「支線素材」挑一條，在正文裡埋**一句**懸念（建議放在結尾互動之前），"
          "只寫「有這件事」、**不寫答案**（答案裡的名字、次數、場次都不能寫）；JSON 另外加 "
          '"hook": {"branch": "B1", "teaser": "那一句，要跟正文一字不差"}。一篇只能一個伏筆')


def prepare(branches: list[dict] | None, facts: list[str]) -> list[dict]:
    """主線事實裡本來就有的名字不當答案關鍵字（例：FIKRI 是馬丁的前搭檔，正文本來就要寫）；
    這種答案只能靠編輯（R16）判斷有沒有把「誰贏了幾次」講出來。"""
    text = "\n".join(facts)
    return [{**b, "keys": [k for k in b["keys"] if k and k not in text]} for b in branches or []]


def editor_context(branches: list[dict]) -> str:
    """給編輯的補充說明（R16）：支線答案不能出現在正文。"""
    if not branches:
        return ""
    return "伏筆支線答案（R16：正文只能有懸念，不能出現這些）：" + "；".join(
        f"B{i}「{b['title']}」答案：{'；'.join(b['answer'])[:200]}" for i, b in enumerate(branches, 1))


def _norm(t: str) -> str:
    return re.sub(r"\s", "", t or "")


def branches_block(branches: list[dict]) -> list[str]:
    """事實清單的支線素材區：「B1｜懸念方向：…｜答案事實：…」（答案只給寫手參考，不能寫進正文）。"""
    if not branches:
        return []
    out = ["【支線素材（伏筆用：挑一條，只寫懸念、不寫答案）】"]
    for i, b in enumerate(branches, 1):
        out.append(f"B{i}｜懸念方向：{b['hint']}｜答案事實（不能寫進正文）：{'；'.join(b['answer'])}")
    return out


def check(out_hook: dict | None, text: str, branches: list[dict]) -> list[str]:
    """伏筆檢查（R16）：只能一個、要真的在正文裡、正文不可出現答案關鍵字。沒有支線素材時不檢查。"""
    if not branches:
        return []
    if not out_hook or not isinstance(out_hook, dict):
        return ["沒有伏筆（R16：挑一條支線素材埋一句懸念，輸出 hook）"]
    if isinstance(out_hook.get("teaser"), list):
        return ["伏筆只能一個"]
    b = out_hook.get("branch", "")
    if not (b.startswith("B") and b[1:].isdigit() and 1 <= int(b[1:]) <= len(branches)):
        return [f"伏筆用了不存在的支線 {b}"]
    teaser = (out_hook.get("teaser") or "").strip()
    if not teaser or _norm(teaser) not in _norm(text):
        return ["伏筆句不在正文裡"]
    leaked = [k for k in branches[int(b[1:]) - 1]["keys"] if k and k in text]
    if leaked:
        return [f"伏筆洩漏了答案：{'、'.join(leaked[:4])}（R16：只寫懸念、不寫答案）"]
    return []


def save_from_output(con, out: dict, branches: list[dict], source_kind: str, source_ref: str) -> int | None:
    h = out.get("hook")
    if not branches or not h or not str(h.get("branch", "")).startswith("B"):
        return None
    b = branches[int(h["branch"][1:]) - 1]
    return add(con, source_kind, source_ref, b.get("subject", []), b["title"], h["teaser"], b["answer"])
