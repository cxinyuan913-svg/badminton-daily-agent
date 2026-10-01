"""LLM 介面（已決議用雲端 API，程式包一層介面，之後可換模型）。

依用途選模型（2026-09-30 Raymond 決議）：
  routine（每天、量大、格式固定：今日重點、新聞一句話重點、暱稱擷取）→ LLM_MODEL_ROUTINE，預設 claude-sonnet-5-5
  heavy（一次性、需要推理：賽前分析、影片草稿、驗證報告差異分析）   → LLM_MODEL_HEAVY，預設 claude-opus-5-5
每次呼叫把用途、實際回應的模型、token 寫進 llm_call 表，之後才能算每月費用。

LLM 只能改寫、不能新增事實；輸出裡的數字與英文名字要能在原始資料找到，
找不到的標成「⚠️待確認」給 Raymond 看（CLAUDE.md：事實正確優先）。

沒有設定 ANTHROPIC_API_KEY 時 available() 回傳 False，摘要照常推送、只是沒有重點段落。
"""
from __future__ import annotations

import os
import re
from typing import Protocol

from brief.discord import ENV_FILE

MODELS = {"routine": ("LLM_MODEL_ROUTINE", "claude-sonnet-5-5"), "heavy": ("LLM_MODEL_HEAVY", "claude-opus-5-5")}
EFFORT = {"routine": "medium", "heavy": "high"}
# 每百萬 token 美元（輸入, 輸出），2026-09 定價；用來估算費用
PRICE = {"claude-sonnet-5-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0), "claude-haiku-4-5": (1.0, 5.0),
         "claude-fable-5-1": (10.0, 50.0), "claude-opus-4-8": (5.0, 25.0)}

CALL_TABLE = """
CREATE TABLE IF NOT EXISTS llm_call (
    call_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    called_at       TEXT NOT NULL DEFAULT (datetime('now')),
    purpose         TEXT NOT NULL,                -- routine / heavy
    task            TEXT,                         -- highlight / news_gist / nickname …
    model           TEXT NOT NULL,                -- 實際回應的模型（fallback 時可能不同）
    input_tokens    INTEGER NOT NULL,
    output_tokens   INTEGER NOT NULL,
    cost_usd        REAL                          -- 依 PRICE 估算；未知模型為 NULL
);
"""

HIGHLIGHT_SYSTEM = """你是羽球日報的編輯助理，讀者是台灣的羽球愛好者，審稿人是前職業選手。
根據使用者提供的賽果摘要，用繁體中文、台灣用語寫 2–4 句「今日重點」。
規則：
- 只能使用摘要裡出現的事實：選手名字、國家、比分、排名、輪次一律照抄，不可推測或補充背景
- 選手名字照摘要的寫法：摘要是「中文（英文）」就寫「中文（英文）」；摘要只有英文就只寫英文，絕對不要自行翻譯或音譯成中文
- 優先寫：爆冷、中華台北選手、決賽與四強
- 「爆冷」「冷門」只能用在摘要裡標了 ⚡爆冷 或 💥大爆冷 的場次；「逆轉」「三局大戰」這類能由比分看出的詞可以用
- 摘要的比分是勝方在前；句子的主詞是敗方時，比分要倒過來寫成主詞的角度（例如「周天成以 10-21 11-21 不敵…」）
- 摘要裡沒有的賽事、項目、比分一律不要提（例如摘要沒有團體賽，就不要寫團體賽）
- 摘要裡沒有賽果時，只寫一句「今天沒有新的賽果。」
- 先在心裡逐句核對，再輸出；只輸出一段最終文字，不要標題、不要列點、不要解釋或更正"""

NEWS_SYSTEM = """把使用者提供的羽球新聞英文標題，用繁體中文、台灣用語寫成一句重點（30 字以內）。
只能根據標題本身，不可補充標題沒有的內容。選手名字保留英文，不要翻譯或音譯。只輸出那一句。"""


class LLM(Protocol):
    def complete(self, system: str, user: str) -> str: ...


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    if not value and ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            key, sep, val = line.partition("=")
            if sep and key.strip() == name:
                value = val.strip().strip('"').strip("'")
    return value or None


def model_for(purpose: str) -> str:
    env, default = MODELS[purpose]
    return _env(env) or default


def cost(model: str, input_tokens: int, output_tokens: int) -> float | None:
    price = PRICE.get(model)
    return None if price is None else (input_tokens * price[0] + output_tokens * price[1]) / 1_000_000


def log_call(con, purpose: str, task: str | None, model: str, input_tokens: int, output_tokens: int) -> None:
    con.executescript(CALL_TABLE)
    con.execute("INSERT INTO llm_call (purpose, task, model, input_tokens, output_tokens, cost_usd) VALUES (?,?,?,?,?,?)",
                (purpose, task, model, input_tokens, output_tokens, cost(model, input_tokens, output_tokens)))
    con.commit()


class AnthropicLLM:
    """purpose 決定模型；con 有給就記錄每次呼叫的用量。task 是呼叫端設定的標籤（記錄用）。"""

    def __init__(self, purpose: str = "routine", con=None, api_key: str | None = None):
        import anthropic
        self.client = anthropic.Anthropic(api_key=api_key or _env("ANTHROPIC_API_KEY"))
        self.purpose, self.model, self.con, self.task = purpose, model_for(purpose), con, None

    def complete(self, system: str, user: str) -> str:
        # 伺服器端 fallback：安全分類器拒答時，由 API 自動改用其他模型完成同一個請求
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            system=system,
            output_config={"effort": EFFORT[self.purpose]},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": user}],
        )
        if self.con is not None:
            log_call(self.con, self.purpose, self.task, response.model,
                     response.usage.input_tokens, response.usage.output_tokens)
        if response.stop_reason == "refusal":
            raise RuntimeError(f"LLM 拒答：{getattr(response.stop_details, 'category', None)}")
        return "".join(b.text for b in response.content if b.type == "text").strip()


def available() -> bool:
    return _env("ANTHROPIC_API_KEY") is not None


# ---------------------------------------------------------------- 事實檢查
NUMBER = re.compile(r"\d+")
SCORE = re.compile(r"(\d{1,2})\s*(?:[-–—:：]|比)\s*(\d{1,2})(?!\d)")
# 模型把「自我更正」也寫出來時（2026-09-30 實測），整段不採用
META = re.compile(r"等一下|更正|修正後|我寫錯|寫錯了|以下是|抱歉")
LATIN_NAME = re.compile(r"[A-Z][A-Za-z'.-]+(?: [A-Z][A-Za-z'.-]+)*")
IGNORE_LATIN = {"MS", "WS", "MD", "WD", "XD", "QF", "SF", "Final", "IC", "IS", "BWF", "TPE"}


CJK_PAIR = re.compile(r"([一-鿿]{2,4})（([A-Za-z][^）]*)）")


def _zh_table() -> dict[str, str]:
    """台灣選手中文名＋外國選手 confirmed 譯名：輸出裡出現的這些名字都必須在原始資料裡。"""
    from brief.zh import load_player_table
    names = {r["name_zh"]: r["name_en"] for r in load_player_table()}
    try:
        import sqlite3
        from brief.discord import ENV_FILE
        db = ENV_FILE.parent / "data" / "brief.db"
        if db.exists():
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            names.update(dict(con.execute("SELECT name_zh, name_en FROM foreign_name WHERE status='confirmed'")))
    except Exception:  # noqa: BLE001 — 沒有譯名表時只用台灣名單
        pass
    return names


def unverified(text: str, source: str, zh_names: dict[str, str] | None = None) -> list[str]:
    """LLM 輸出中，在原始資料找不到的數字與名字。
    名字：英文要出現在原始資料；「中文（英文）」的中文要在對照表裡、且對應到同一個英文；
    對照表裡的中文名出現在輸出，也要出現在原始資料。"""
    zh_names = _zh_table() if zh_names is None else zh_names
    # 比分整組比對：「21-15」「21 比 15」「3–2」都要在原始資料有同一組（正反順序皆可，敗方角度會倒過來寫）。
    # 2026-09-30 實測：LLM 捏造「印尼 3–2 中國」，拆成單獨數字比對會被 3、2 矇混過去
    scores = {tuple(m) for m in SCORE.findall(source)}
    scores |= {(b, a) for a, b in scores}
    missing = []
    for a, b in SCORE.findall(text):
        if (a, b) not in scores and f"{a}-{b}" not in missing:
            missing.append(f"{a}-{b}")
    # 其餘數字要整個比對（否則「5」會在「21-15」裡被找到）
    numbers = set(NUMBER.findall(SCORE.sub(" ", source))) | {x for pair in scores for x in pair}
    for token in NUMBER.findall(SCORE.sub(" ", text)):
        if token not in numbers and token not in missing:
            missing.append(token)
    for token in LATIN_NAME.findall(text):
        if token in IGNORE_LATIN or token in source:
            continue
        if token not in missing:
            missing.append(token)
    for name_zh, name_en in CJK_PAIR.findall(text):
        if zh_names.get(name_zh) != name_en:
            missing.append(f"{name_zh}（{name_en}）對照表不符")
    for name_zh in zh_names:
        if name_zh in text and name_zh not in source and name_zh not in missing:
            missing.append(name_zh)
    return missing


UPSET_WORD = re.compile(r"爆冷|冷門")
SENTENCE = re.compile(r"[^。；;！!？?\n]+")


def unlicensed_upsets(text: str, source: str, zh_names: dict[str, str] | None = None) -> list[str]:
    """2026-09-30 22:45 決議：「爆冷」只能用在 upset_level 判定的場次（原始資料該行有 ⚡爆冷／💥大爆冷）。
    回傳用了「爆冷／冷門」、但提到的選手都不在爆冷場次裡的句子。"""
    zh_names = _zh_table() if zh_names is None else zh_names
    upset_lines = [l for l in source.splitlines() if "爆冷" in l]
    bad = []
    for sent in SENTENCE.findall(text):
        if not UPSET_WORD.search(sent):
            continue
        names = [n for n in LATIN_NAME.findall(sent) if n not in IGNORE_LATIN] + [z for z in zh_names if z in sent]
        if not any(n in line for n in names for line in upset_lines):
            bad.append(sent.strip())
    return bad


def news_gist(llm: LLM, title: str) -> str:
    if hasattr(llm, "task"):
        llm.task = "news_gist"
    return llm.complete(NEWS_SYSTEM, title).strip().splitlines()[0]


def highlight(llm: LLM, digest_text: str) -> str:
    """回傳要放在摘要最上方的段落；有查不到的內容就附上待確認清單。"""
    if hasattr(llm, "task"):
        llm.task = "highlight"
    text = re.sub(r"^\**今日重點\**[:：]\s*", "", llm.complete(HIGHLIGHT_SYSTEM, digest_text).strip())   # 模型自己加的標題
    if "\n\n" in text or META.search(text):
        raise ValueError(f"今日重點格式異常，未採用：{text[:80]}…")
    bad = unlicensed_upsets(text, digest_text)
    if bad:
        raise ValueError(f"今日重點把規則沒判定的場次寫成爆冷，未採用：{bad[0][:60]}")
    missing = unverified(text, digest_text)
    lines = [f"**今日重點**（AI 整理，請審稿）\n{text}"]
    if missing:
        lines.append("⚠️待確認（原始資料查不到）：" + "、".join(missing))
    return "\n".join(lines)
