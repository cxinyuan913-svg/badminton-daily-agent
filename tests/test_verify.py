"""獨立查證員（notes 11:25 B）：用錄好的回應重播（不呼叫 API、不需要正式資料庫）。"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from brief import crawler, verify

FIX = Path(__file__).parent / "fixtures" / "verify_carnando.json"


def _block(d):
    return SimpleNamespace(**d)


class Replay:
    """照順序回傳錄好的回應；記下每次送出的 messages。"""
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []
        self.beta = self
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        r = self.responses.pop(0)
        return SimpleNamespace(stop_reason=r["stop_reason"], model=r["model"], usage=SimpleNamespace(**r["usage"]),
                               content=[_block(b) for b in r["content"]])


@pytest.fixture
def empty_db(tmp_path):
    db = tmp_path / "brief.db"
    crawler.connect(str(db)).close()                     # 只有表格結構，沒有資料
    return db


def test_regression_157_is_wrong_and_others_ok(empty_db):
    """11:25 回歸：「重新組隊時，排名只剩第一百五十七」必須判 wrong，證據顯示 2026-05-12 前沒有排名；世界第九、2024 拆夥、泰國公開賽奪冠判 ok。"""
    rec = json.loads(FIX.read_text(encoding="utf-8"))
    res = verify.verify(rec["text"], client=Replay(rec["responses"]), db=empty_db)
    by = {c["claim"]: c for c in res["claims"]}
    w = [c for c in res["claims"] if "百五十七" in c["claim"]]
    assert w and w[0]["verdict"] == "wrong" and "2026-05-12" in w[0]["note"] and "沒有這組" in w[0]["note"]
    for key in ("世界第九", "2024 年兩人拆夥", "拿下冠軍"):
        hit = [c for c in res["claims"] if key in c["claim"]]
        assert hit and all(c["verdict"] == "ok" for c in hit), key
    assert res["summary"].startswith("查證 7 條") and "1 錯" in res["summary"]
    assert verify.wrong(res) == w and "157" in verify.feedback(res)
    assert len(by) == 7


def test_verifier_never_sees_writer_prompt_or_facts(empty_db):
    rec = json.loads(FIX.read_text(encoding="utf-8"))
    client = Replay(rec["responses"])
    verify.verify(rec["text"], client=client, db=empty_db)
    first = client.calls[0]["messages"][0]["content"]
    assert "事實清單" not in first and "F1" not in first and rec["text"] in first
    assert client.calls[0]["tools"][0]["name"] == "sql"


def test_sql_tool_is_read_only_and_limited(empty_db):
    t = verify.ReadOnlySQL(empty_db, max_queries=2, row_limit=200)
    assert "只允許單一 SELECT" in t.run("DELETE FROM match")
    assert "只允許單一 SELECT" in t.run("SELECT 1; DROP TABLE match")
    assert "SQL 錯誤" in t.run("UPDATE match SET round='x'") or "只允許" in t.run("UPDATE match SET round='x'")
    out = t.run("WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x < 500) SELECT x FROM n")
    assert "只顯示前 200 列" in out
    assert "用完 2 次查詢" in t.run("SELECT 1")
