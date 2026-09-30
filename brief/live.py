"""羽球日報 Agent：取得「目前進行中」的賽事，交給每日排程去爬。

來源（2026-09-30 實測）：
  https://extranet-lv.bwfbadminton.com/api/match-center/vue-current-live?showpara=0
  回傳 JSON：賽事 id、GUID、名稱、起訖日期，不必解析 HTML。

注意：這裡的 tournament_category_id 與行事曆分類 API 的 id 不是同一套編號
（North Harbour International 是 5），所以層級仍以 scanner 的冠軍積分判斷為準。

用法：python -m brief.live            # 列出進行中且在追蹤範圍內的賽事
"""
from __future__ import annotations

import re

from brief.crawler import API, Client
from brief.scanner import EXCLUDE

LIVE_URL = f"{API}/match-center/vue-current-live"


def parse_live(payload: dict) -> list[dict]:
    """轉成爬蟲用的格式，並排除青少年、團體、元老等不在範圍內的賽事。"""
    out = []
    for t in payload.get("results", []):
        name = t.get("name") or ""
        if EXCLUDE.search(name):
            continue
        out.append({
            "tournament_id": int(t["id"]),
            "code": (t.get("code") or "").upper() or None,
            "name": name,
            "start_date": (t.get("start_date") or "")[:10] or None,
            "end_date": (t.get("end_date") or "")[:10] or None,
            "source_url": t.get("tmtLink"),
        })
    return out


def current_live(client: Client) -> list[dict]:
    r = client.get(LIVE_URL, showpara=0)
    return parse_live(r.json()) if r is not None else []


def main():
    for t in current_live(Client()):
        print(t["tournament_id"], t["start_date"], "→", t["end_date"], t["name"])


if __name__ == "__main__":
    main()
