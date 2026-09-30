"""羽球日報 Agent：每日收集（P1 排程每天 06:00 Asia/Taipei 執行這支）

流程：
  1. 年度賽程（calendar）：更新今年的賽事清單，挑出「最近幾天有比賽」的賽事
  2. 進行中賽事（live）：交叉檢查，補上賽程日期對不上、但在追蹤範圍內的賽事
  3. 賽果（crawler）：只抓每站最近 LOOKBACK_DAYS 天，漏跑一兩天也補得回來
  4. 新聞（news）：BWF 與台灣媒體的列表頁
  5. 排名（rankings）：API 有新的一週就存下來（API 只留約 60 週，漏了就永久遺失）
  6. 執行紀錄寫進 crawl_run

追蹤範圍以 calendar 為準：live 的名稱篩選會把湯尤盃等團體賽排除，不能拿來判斷範圍。

用法：
  python -m brief.daily --db data/brief.db
  python -m brief.daily --db data/brief.db --date 2026-09-30 --lookback 3   # 補跑指定日期
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import traceback

from brief import calendar, crawler, live, news, rankings
from brief.crawler import Client, connect

TAIPEI = dt.timezone(dt.timedelta(hours=8))   # 台灣沒有日光節約時間，用固定時差即可
LOOKBACK_DAYS = 2                             # 除了今天，再往回抓幾天
SKIP_STATUS = {"cancelled", "postponed"}


def today_taipei() -> dt.date:
    return dt.datetime.now(TAIPEI).date()


def days_to_crawl(t: dict, today: dt.date, lookback: int | None = LOOKBACK_DAYS) -> list[str]:
    """賽事日期與 [today - lookback, today] 的交集。美洲的比賽在台北隔天早上才結束，所以要往回抓。
    lookback=None 表示從開賽日抓起（資料庫還沒有這站時）。"""
    if not (t.get("start_date") and t.get("end_date")):
        return []
    start = dt.date.fromisoformat(t["start_date"])
    if lookback is not None:
        start = max(start, today - dt.timedelta(days=lookback))
    end = min(dt.date.fromisoformat(t["end_date"]), today)
    return list(crawler.dates_between(start.isoformat(), end.isoformat())) if start <= end else []


def select_targets(calendar_rows: list[dict], live_rows: list[dict], today: dt.date,
                   lookback: int = LOOKBACK_DAYS, crawled: set[int] = frozenset()) -> list[tuple[dict, list[str]]]:
    """回傳 [(賽事, 要抓的日期)]。
    - 賽程裡、未取消、日期落在範圍內的賽事
    - live 裡有、而且在賽程追蹤範圍內的賽事（日期以 live 為準，賽程日期偶爾會跟實際不同）
    crawled 是資料庫裡已有比賽的賽事；不在裡面的從開賽日整站補抓。"""
    tracked = {r["tournament_id"]: r for r in calendar_rows if r.get("status") not in SKIP_STATUS}
    targets: dict[int, tuple[dict, list[str]]] = {}

    def window(tid: int, t: dict) -> list[str]:
        if not days_to_crawl(t, today, lookback):
            return []
        return days_to_crawl(t, today, lookback if tid in crawled else None)

    for tid, t in tracked.items():
        days = window(tid, t)
        if days:
            targets[tid] = (t, days)
    for lv in live_rows:
        tid = lv["tournament_id"]
        if tid not in tracked:
            continue
        t = {**tracked[tid], **{k: v for k, v in lv.items() if v}}
        days = sorted(set(window(tid, t)) | set(targets.get(tid, (None, []))[1]))
        if days:
            targets[tid] = (t, days)
    return [targets[k] for k in sorted(targets)]


def calendar_years(today: dt.date) -> list[int]:
    """一月初要連去年一起看，跨年賽事（12 月開打）才不會漏。"""
    return [today.year - 1, today.year] if today.month == 1 else [today.year]


def run(con, client: Client, today: dt.date, lookback: int = LOOKBACK_DAYS, verbose=True) -> dict:
    run_id = con.execute("INSERT INTO crawl_run (run_date) VALUES (?)", (today.isoformat(),)).lastrowid
    con.commit()
    errors: list[str] = []
    stored = ranking_rows = 0
    done: list[int] = []

    cal_rows: list[dict] = []
    for y in calendar_years(today):
        try:
            rows = calendar.fetch_year(client, y)
            for r in rows:
                crawler.upsert_tournament(con, r)
            con.commit()
            cal_rows += rows
        except Exception as e:  # noqa: BLE001 — 每一步獨立，一步失敗不擋後面
            errors.append(f"calendar {y}: {e!r}")
    try:
        live_rows = live.current_live(client)
    except Exception as e:  # noqa: BLE001
        live_rows = []
        errors.append(f"live: {e!r}")

    crawled = {r[0] for r in con.execute("SELECT DISTINCT tournament_id FROM match")}
    for t, days in select_targets(cal_rows, live_rows, today, lookback, crawled):
        try:
            if verbose:
                print(f"[{t['tournament_id']}] {t['name']}（{days[0]} → {days[-1]}）")
            res = crawler.crawl_known(client, con, t, verbose=verbose, days=days)
            stored += res["stored"]
            done.append(t["tournament_id"])
        except Exception as e:  # noqa: BLE001
            errors.append(f"crawler {t['tournament_id']}: {e!r}")
            if verbose:
                traceback.print_exc()

    try:
        _, news_errors = news.collect(client, con)
        errors += news_errors
    except Exception as e:  # noqa: BLE001
        errors.append(f"news: {e!r}")

    try:
        for w in rankings.weeks_missing(con, rankings.list_weeks(client)):
            ranking_rows += rankings.crawl_week(client, con, w, verbose=verbose)
    except Exception as e:  # noqa: BLE001
        errors.append(f"rankings: {e!r}")

    con.execute(
        """UPDATE crawl_run SET finished_at=datetime('now'), tournaments=?, matches_stored=?,
                                ranking_rows=?, errors=? WHERE run_id=?""",
        (",".join(map(str, done)), stored, ranking_rows, "\n".join(errors) or None, run_id))
    con.commit()
    return {"run_id": run_id, "tournaments": done, "matches_stored": stored,
            "ranking_rows": ranking_rows, "errors": errors}


def publish(con, today: dt.date, res: dict) -> list[str]:
    """推送每日摘要；收集有錯誤時推到告警頻道。回傳推送本身的錯誤。"""
    from brief import digest, discord
    errors = []
    try:
        from brief import llm
        model = llm.AnthropicLLM() if llm.available() else None
        text, matches, ties, news_rows = digest.build(con, today, llm=model, errors=errors)
        discord.send(discord.webhook("DISCORD_WEBHOOK_DAILY"), text)
        digest.mark_sent(con, today, matches, ties, news_rows)
    except Exception as e:  # noqa: BLE001
        errors.append(f"digest: {e!r}")
    problems = res["errors"] + errors
    if problems:
        try:
            discord.send(discord.webhook("DISCORD_WEBHOOK_ALERTS"),
                         f"**每日收集 {today.isoformat()} 有 {len(problems)} 個錯誤**\n" +
                         "\n".join(f"- `{p[:300]}`" for p in problems))
        except Exception as e:  # noqa: BLE001
            errors.append(f"alert: {e!r}")
    return errors


def main():
    ap = argparse.ArgumentParser(description="每日收集：賽程 → 賽果 → 排名")
    ap.add_argument("--db", default="brief.db")
    ap.add_argument("--date", help="指定「今天」（YYYY-MM-DD），補跑用；預設為台北今天")
    ap.add_argument("--lookback", type=int, default=LOOKBACK_DAYS)
    ap.add_argument("--send", action="store_true", help="收集完推送每日摘要到 Discord；有錯誤時推到告警頻道")
    a = ap.parse_args()
    today = dt.date.fromisoformat(a.date) if a.date else today_taipei()
    con = connect(a.db)
    res = run(con, Client(), today, a.lookback)
    print(f"完成：{len(res['tournaments'])} 站、{res['matches_stored']} 場、排名 {res['ranking_rows']} 筆")
    errors = res["errors"] + (publish(con, today, res) if a.send else [])
    for e in errors:
        print("錯誤：", e, file=sys.stderr)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
