"""獨立查證員（notes 10-02 11:25 B）：看不到寫手的提示詞與事實清單，只拿最終文稿＋唯讀 SQL 工具，逐條查證。

  - 模型 Opus low；工具 `sql`：sqlite3 以 mode=ro 開 data/brief.db，只允許單一 SELECT／WITH，
    單次結果上限 200 列、每支最多 15 次查詢
  - 輸出 [{claim, verdict: ok|wrong|unverifiable, evidence_sql, evidence_rows, note}]
  - 有 wrong → 呼叫端把查證結果交回寫手重寫一次 → 再查一次；仍有 wrong 就不發，改發告警頻道附查證表
  - unverifiable 不擋，但列進給 Raymond 的待查清單
  - 費用記 llm_call（task＝verify）；每支超過 US$0.15 就降低查詢上限（status.md 回報）
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "brief.db"
MAX_QUERIES = 15
ROW_LIMIT = 200
TABLES = ("tournament", "match", "game", "pairing", "player", "ranking_snapshot", "tournament_result", "team_tie")

SYSTEM = """你是羽球資料的查證員。使用者給你一篇已經寫好的稿子（影片腳本或 FB 貼文），你的工作是**找錯**，不是潤稿。
1. 把稿子拆成可以查證的主張（比分、排名、名次、日期、場數、交手紀錄、搭檔、誰贏誰、第幾站、時間點）。修辭、感想、問句不用拆。
2. 每條主張用 sql 工具查資料庫確認（只能 SELECT；每次最多回 200 列；整篇最多 15 次查詢，請合併查詢、先查最關鍵的）。
3. 特別注意**時間點**：「重組時的排名」要看重組那一站開賽前最後一次發布的排名；「賽前排名」同理；排名表上沒有這組＝沒有排名，不能拿之後的名次。
4. 判斷：ok（資料庫支持）、wrong（資料庫證明是錯的，或時間點／比較對象用錯）、unverifiable（資料庫查不到或沒有這種資料，例如傷病、訪談內容、年齡）。
   不確定就是 unverifiable，不要硬判 wrong。
5. 名字：稿子可能用中文名或英文名；player 表有 name_display（英文）與 name_zh（中文，可能是空的）。
查完只輸出 JSON（不要其他文字）：{"claims": [{"claim": "稿子裡的主張", "verdict": "ok|wrong|unverifiable",
"evidence_sql": "你用來判斷的 SQL", "evidence_rows": "關鍵的幾列結果（文字）", "note": "一句說明"}]}"""

NOTES = """資料表說明：
- tournament(tournament_id, name, level, start_date, end_date)：level 例 S1000、S500、G1_IND（世錦賽）、MULTI（亞運等）、IC、IS
- match(match_id, tournament_id, event, round, match_date, side1_id, side2_id, winner_side, score_status, duration_min, side1_seed, side2_seed)：
  event = MS/WS/MD/WD/XD；round 例 R32、R16、QF、SF、Final；winner_side 1 或 2；side*_id 是 pairing_id
- game(match_id, game_no, side1_points, side2_points)：局分
- pairing(pairing_id, player_a_id, player_b_id)：單打 player_b_id 是 NULL；雙打組合換人就是另一個 pairing
- player(player_id, name_display, name_zh, country_code)
- ranking_snapshot(week_date, event, pairing_id, rank, points, tournaments)：week_date＝官方排名發布日；tournaments＝當時計入站數；
  歷史週（2025-08 以前）只存前 100 名，表上沒有不代表沒有排名
- tournament_result(pairing_id, tournament_id, event, round_reached, result_date)：round_reached W＝冠軍、F＝亞軍、SF＝四強…"""


def schema_text(db: Path = DB) -> str:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute(f"SELECT sql FROM sqlite_master WHERE type='table' AND name IN ({','.join('?' * len(TABLES))})",
                           TABLES).fetchall()
    finally:
        con.close()
    return "\n".join(r[0] for r in rows if r[0]) + "\n\n" + NOTES


class ReadOnlySQL:
    """唯讀 SQL 工具：mode=ro＋authorizer 只放行讀取；只接受單一 SELECT／WITH；結果上限 ROW_LIMIT 列。"""

    def __init__(self, db: Path = DB, max_queries: int = MAX_QUERIES, row_limit: int = ROW_LIMIT):
        self.con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        self.con.set_authorizer(self._auth)
        self.max_queries, self.row_limit, self.used = max_queries, row_limit, 0

    @staticmethod
    def _auth(action, *args):
        allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, getattr(sqlite3, "SQLITE_RECURSIVE", 33)}
        return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY

    def run(self, query: str) -> str:
        if self.used >= self.max_queries:
            return f"錯誤：已經用完 {self.max_queries} 次查詢，請直接輸出結論"
        q = (query or "").strip().rstrip(";")
        if not re.match(r"(?is)^(select|with)\b", q) or ";" in q:
            return "錯誤：只允許單一 SELECT（或 WITH … SELECT）"
        self.used += 1
        try:
            cur = self.con.execute(q)
            cols = [d[0] for d in cur.description or []]
            rows = cur.fetchmany(self.row_limit + 1)
        except sqlite3.Error as e:
            return f"SQL 錯誤：{e}"
        more = len(rows) > self.row_limit
        lines = [" | ".join(cols)] + [" | ".join("" if v is None else str(v) for v in r) for r in rows[:self.row_limit]]
        return "\n".join(lines) + (f"\n（只顯示前 {self.row_limit} 列）" if more else "") + f"\n（第 {self.used}/{self.max_queries} 次查詢）"


TOOLS = [{"name": "sql", "description": "在羽球資料庫（SQLite，唯讀）執行一個 SELECT 查詢，回傳結果表格（最多 200 列）。",
          "input_schema": {"type": "object", "properties": {"query": {"type": "string", "description": "一個 SELECT 查詢"}},
                           "required": ["query"]}}]


def _parse(text: str) -> list[dict]:
    try:
        j = json.loads(text[text.find("{"): text.rfind("}") + 1])
    except ValueError:
        return []
    out = []
    for c in j.get("claims") or []:
        v = c.get("verdict")
        if v in ("ok", "wrong", "unverifiable"):
            out.append({k: c.get(k, "") for k in ("claim", "verdict", "evidence_sql", "evidence_rows", "note")})
    return out


def verify(text: str, client=None, con=None, model: str | None = None, db: Path = DB,
           max_queries: int = MAX_QUERIES, max_turns: int = 25) -> dict:
    """回傳 {claims, summary, queries, cost}。client 預設用真的 Anthropic client；測試傳入錄好的回應。"""
    from brief import llm as llm_mod
    if client is None:
        import anthropic
        client = anthropic.Anthropic(api_key=llm_mod._env("ANTHROPIC_API_KEY"))
    model = model or llm_mod.model_for("heavy")
    tool = ReadOnlySQL(db, max_queries)
    messages = [{"role": "user", "content": f"資料庫結構：\n{schema_text(db)}\n\n要查證的稿子：\n{text}"}]
    cost, final = 0.0, ""
    for _ in range(max_turns):
        resp = client.beta.messages.create(model=model, max_tokens=8000, system=SYSTEM, tools=TOOLS, messages=messages,
                                           output_config={"effort": "low"})
        c = llm_mod.cost(resp.model, resp.usage.input_tokens, resp.usage.output_tokens) or 0.0
        cost += c
        if con is not None:
            llm_mod.log_call(con, "heavy", "verify", resp.model, resp.usage.input_tokens, resp.usage.output_tokens, "low")
        uses = [b for b in resp.content if getattr(b, "type", "") == "tool_use"]
        if resp.stop_reason != "tool_use" or not uses:
            final = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
            break
        messages.append({"role": "assistant", "content": resp.content})
        messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": b.id,
                                                       "content": tool.run((b.input or {}).get("query", ""))} for b in uses]})
    claims = _parse(final)
    return {"claims": claims, "summary": summary(claims), "queries": tool.used, "cost": round(cost, 4)}


def summary(claims: list[dict]) -> str:
    n = {v: sum(1 for c in claims if c["verdict"] == v) for v in ("ok", "wrong", "unverifiable")}
    parts = [f"{n['ok']} ok"] + ([f"{n['wrong']} 錯"] if n["wrong"] else []) + ([f"{n['unverifiable']} 無法查證"] if n["unverifiable"] else [])
    return f"查證 {len(claims)} 條：" + "、".join(parts)


def wrong(result: dict) -> list[dict]:
    return [c for c in result.get("claims", []) if c["verdict"] == "wrong"]


def feedback(result: dict) -> str:
    """交回寫手的查證結果（只給錯的那幾條與證據）。"""
    return "；".join(f"「{c['claim']}」查證為錯：{c['note']}（證據：{str(c['evidence_rows'])[:200]}）" for c in wrong(result))


def save(result: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return path
