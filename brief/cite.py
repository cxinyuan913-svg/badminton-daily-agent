"""逐句引用（notes 10-02 11:25 A；準則 R15「每句可指回事實且意思要對」）。

事實清單每條給穩定 id（F1、F2…）；寫手輸出 sentences: [{text, fact_ids, kind}]，程式再組回全文。
程式檢查（不靠模型）：
  - 含數字、選手名、名次或「第一站／首冠／連勝」這類事實性字眼的句子，fact_ids 不可為空，引用的 id 必須存在
  - 句子裡的數字與名字，要出現在**它引用的那幾條**事實裡（不是整份清單）
  - kind = "rhetoric"（轉場、金句、留言問題）且沒有事實性內容的句子不用引用；帶 ⚠️推測 的句子不檢查
"""
from __future__ import annotations

import re

FACTUAL_WORDS = re.compile(r"冠軍|亞軍|四強|八強|十六強|16 強|32 強|金牌|銀牌|銅牌|第一站|首冠|首座|連勝|連敗|排名|世界第|種子|交手|局數|退賽|逆轉|衛冕")
DIGIT = re.compile(r"\d")
SPEC = "⚠️推測"


def number_facts(facts: list[str]) -> dict[str, str]:
    """{ "F1": 事實, … }：分區標題（【歷史】等）也編號，但不會被當成可引用的事實內容。"""
    return {f"F{i}": f for i, f in enumerate(facts, 1)}


def facts_block(facts: list[str]) -> str:
    return "\n".join(f"{k}: {v}" for k, v in number_facts(facts).items())


def is_factual(text: str, names: list[str]) -> bool:
    from brief import storylines as sl
    if DIGIT.search(text) or FACTUAL_WORDS.search(text):
        return True
    return any(m in text for n in names for m in sl.members(n))


def check(sentences: list[dict], facts: list[str]) -> list[str]:
    """回傳不通過的原因（空 = 通過）。"""
    from brief import storylines as sl
    from brief.llm import unverified
    if not sentences:
        return ["格式不對：沒有 sentences（逐句引用）"]
    ids = number_facts(facts)
    names = sl.side_names(facts)
    problems = []
    for s in sentences:
        text = (s.get("text") or "").strip()
        if not text or SPEC in text:
            continue
        cited = [i for i in (s.get("fact_ids") or [])]
        missing_ids = [i for i in cited if i not in ids]
        if missing_ids:
            problems.append(f"引用了不存在的事實 {'、'.join(missing_ids)}：「{text[:30]}」")
            continue
        if not is_factual(text, names):
            continue
        if not cited:
            problems.append(f"有事實性內容卻沒有引用：「{text[:40]}」")
            continue
        bad = unverified(text, "\n".join(ids[i] for i in cited))
        if bad:
            problems.append(f"「{text[:40]}」的 {'、'.join(bad[:4])} 不在它引用的 {'、'.join(cited)} 裡")
    return problems


def assemble(sentences: list[dict]) -> str:
    return "".join((s.get("text") or "").strip() for s in sentences)


def annotated(sentences: list[dict], facts: list[str]) -> str:
    """給編輯看的逐句對照：句子＋它引用的事實全文（R15：句意和引用事實是否一致，特別是時間點）。"""
    ids = number_facts(facts)
    out = []
    for s in sentences:
        cited = [i for i in (s.get("fact_ids") or []) if i in ids]
        refs = "；".join(f"{i}={ids[i]}" for i in cited) or ("（修辭）" if s.get("kind") == "rhetoric" else "（沒有引用）")
        out.append(f"・{(s.get('text') or '').strip()}\n    ↳ {refs}")
    return "\n".join(out)
