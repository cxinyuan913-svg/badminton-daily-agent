"""LLM 介面（已決議用雲端 API，程式包一層介面，之後可換模型）。

目前只有一個用途：根據 digest 整理好的事實，寫一段繁體中文「今日重點」。
LLM 只能改寫、不能新增事實；輸出裡的數字與英文名字要能在原始資料找到，
找不到的標成「⚠️待確認」給 Raymond 看（CLAUDE.md：事實正確優先）。

沒有設定 ANTHROPIC_API_KEY 時 available() 回傳 False，摘要照常推送、只是沒有重點段落。
"""
from __future__ import annotations

import os
import re
from typing import Protocol

from brief.discord import ENV_FILE

MODEL = "claude-opus-5-5"

HIGHLIGHT_SYSTEM = """你是羽球日報的編輯助理，讀者是台灣的羽球愛好者，審稿人是前職業選手。
根據使用者提供的賽果摘要，用繁體中文、台灣用語寫 2–4 句「今日重點」。
規則：
- 只能使用摘要裡出現的事實：選手名字、國家、比分、排名、輪次一律照抄，不可推測或補充背景
- 選手名字照摘要的寫法：摘要是「中文（英文）」就寫「中文（英文）」；摘要只有英文就只寫英文，絕對不要自行翻譯或音譯成中文
- 優先寫：爆冷、中華台北選手、決賽與四強
- 摘要裡沒有賽果時，只寫一句「今天沒有新的賽果。」
- 只輸出重點段落本身，不要標題、不要列點"""

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


class AnthropicLLM:
    def __init__(self, model: str = MODEL, api_key: str | None = None):
        import anthropic
        self.client = anthropic.Anthropic(api_key=api_key or _env("ANTHROPIC_API_KEY"))
        self.model = model

    def complete(self, system: str, user: str) -> str:
        # 伺服器端 fallback：安全分類器拒答時，由 API 自動改用其他模型完成同一個請求
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            system=system,
            output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": user}],
        )
        if response.stop_reason == "refusal":
            raise RuntimeError(f"LLM 拒答：{getattr(response.stop_details, 'category', None)}")
        return "".join(b.text for b in response.content if b.type == "text").strip()


def available() -> bool:
    return _env("ANTHROPIC_API_KEY") is not None


# ---------------------------------------------------------------- 事實檢查
NUMBER = re.compile(r"\d+(?:-\d+)*")
LATIN_NAME = re.compile(r"[A-Z][A-Za-z'.-]+(?: [A-Z][A-Za-z'.-]+)*")
IGNORE_LATIN = {"MS", "WS", "MD", "WD", "XD", "QF", "SF", "Final", "IC", "IS", "BWF", "TPE"}


CJK_PAIR = re.compile(r"([一-鿿]{2,4})（([A-Za-z][^）]*)）")


def _zh_table() -> dict[str, str]:
    from brief.zh import load_player_table
    return {r["name_zh"]: r["name_en"] for r in load_player_table()}


def unverified(text: str, source: str, zh_names: dict[str, str] | None = None) -> list[str]:
    """LLM 輸出中，在原始資料找不到的數字與名字。
    名字：英文要出現在原始資料；「中文（英文）」的中文要在對照表裡、且對應到同一個英文；
    對照表裡的中文名出現在輸出，也要出現在原始資料。"""
    zh_names = _zh_table() if zh_names is None else zh_names
    # 數字要整個比對（否則「5」會在「21-15」裡被找到）；比分也拆成單局，LLM 可能寫成「21 比 15」
    numbers = set()
    for token in NUMBER.findall(source):
        numbers.add(token)
        numbers.update(token.split("-"))
    missing = []
    for token in NUMBER.findall(text):
        if not (token in numbers or all(part in numbers for part in token.split("-"))) and token not in missing:
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


def news_gist(llm: LLM, title: str) -> str:
    return llm.complete(NEWS_SYSTEM, title).strip().splitlines()[0]


def highlight(llm: LLM, digest_text: str) -> str:
    """回傳要放在摘要最上方的段落；有查不到的內容就附上待確認清單。"""
    text = llm.complete(HIGHLIGHT_SYSTEM, digest_text)
    missing = unverified(text, digest_text)
    lines = [f"**今日重點**（AI 整理，請審稿）\n{text}"]
    if missing:
        lines.append("⚠️待確認（原始資料查不到）：" + "、".join(missing))
    return "\n".join(lines)
