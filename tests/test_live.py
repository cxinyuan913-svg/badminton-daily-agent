import json
from pathlib import Path

from brief import live

FIX = json.loads((Path(__file__).parent / "fixtures" / "current_live_2026-09-30.json").read_text(encoding="utf-8"))


def test_parse_live_keeps_senior_individual_only():
    rows = live.parse_live(FIX)
    assert [r["tournament_id"] for r in rows] == [5766]          # 非洲青少年團體賽被排除
    assert rows[0]["code"] == "1B95960C-1C1B-41E2-B7CF-120E8CA38CE3"
    assert (rows[0]["start_date"], rows[0]["end_date"]) == ("2026-09-30", "2026-10-04")
