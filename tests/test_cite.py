"""逐句引用（notes 11:25 A）：程式檢查句子與它引用的事實。"""
from brief import cite

FACTS = ["【歷史】",
         "CARNANDO／MARTHIN生涯最高官方排名：世界第 9（2023-05-23 發布｜當時累計 16 站，首次達到）",
         "重組時（2026 泰國公開賽賽前）組合排名：不在排名表上：沒有排名（unranked）（2026-05-05 發布）",
         "組合官方排名 #157｜發布 2026-05-19｜累計 1 站｜這是 2026 泰國公開賽冠軍入帳後第一週"]


def test_number_must_be_in_cited_fact():
    """11:25 測試：一句含「157」卻引用沒有 157 的事實 → 擋下。"""
    bad = [{"text": "重新組隊時，排名只剩第 157。", "fact_ids": ["F3"]}]
    assert any("157" in p and "F3" in p for p in cite.check(bad, FACTS))
    ok = [{"text": "泰國公開賽奪冠後那週，排名是第 157。", "fact_ids": ["F4"]}]
    assert cite.check(ok, FACTS) == []


def test_factual_sentence_needs_citation_and_ids_exist():
    assert any("沒有引用" in p for p in cite.check([{"text": "他們曾經是世界第 9。", "fact_ids": []}], FACTS))
    assert any("不存在" in p for p in cite.check([{"text": "世界第 9。", "fact_ids": ["F99"]}], FACTS))
    rhetoric = [{"text": "你覺得默契回得來嗎？", "fact_ids": [], "kind": "rhetoric"}]
    assert cite.check(rhetoric, FACTS) == []
    assert cite.check([{"text": "可能是傷病 ⚠️推測", "fact_ids": []}], FACTS) == []
    assert cite.check([], FACTS)


def test_assemble_and_annotated():
    s = [{"text": "他們曾是世界第 9。", "fact_ids": ["F2"]}, {"text": "你覺得呢？", "fact_ids": [], "kind": "rhetoric"}]
    assert cite.assemble(s) == "他們曾是世界第 9。你覺得呢？"
    ann = cite.annotated(s, FACTS)
    assert "F2=CARNANDO" in ann and "（修辭）" in ann


OPP = FACTS + ["【關鍵對手：KIM Won Ho / SEO Seung Jae】賽前排名：世界第 1",
               "CARNANDO／MARTHIN與KIM Won Ho / SEO Seung Jae近 12 個月交手 1 次：2026-09-01 中國大師賽 32 強 KIM Won Ho / SEO Seung Jae勝",
               "這場（KIM Won Ho / SEO Seung Jae角度 11-21 14-21）是KIM Won Ho / SEO Seung Jae 2025 年以來 15 場敗場裡局分差最懸殊的一場",
               "【新聞】", "（沒有新聞）"]


def test_key_opponent_needs_one_context_sentence():
    """notes 10-02 18:55 A：有【關鍵對手】脈絡時，只引用標題（賽前排名）不夠，至少一句要引用脈絡事實。"""
    assert cite.opponent_ids(OPP) == {"F6": "KIM Won Ho / SEO Seung Jae", "F7": "KIM Won Ho / SEO Seung Jae"}
    only_rank = [{"text": "四強對上世界第 1 的組合。", "fact_ids": ["F5"]}]
    assert any("關鍵對手" in p and "F6" in p for p in cite.check(only_rank, OPP))
    ok = only_rank + [{"text": "這是對手 2025 年以來輸最懸殊的一場。", "fact_ids": ["F7"]}]
    assert cite.check(ok, OPP) == []
    assert cite.opponent_check(only_rank, FACTS) == []          # 沒有關鍵對手 → 不檢查
