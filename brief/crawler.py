"""羽球日報 Agent：BWF 賽果爬蟲原型

資料來源（2026-09-30 實測）：
  - 賽事頁  https://bwfbadminton.com/tournament/{id}/x/
      取得賽事名稱、API 用的 GUID、比賽日期區間（<div class="live-date">）
  - 每日賽果 https://extranet-lv.bwfbadminton.com/api/tournaments/day-matches
      ?tournamentCode={GUID}&date=YYYY-MM-DD&order=2&court=0
      回傳 JSON：每場比賽、選手 BWF ID、每局比分、勝方
  Grade 1、2、3 都是同一套 API，所以同一支爬蟲通用。

用法：
  python -m brief.crawler 5766                 # 抓一站
  python -m brief.crawler 5766 --db brief.db   # 指定資料庫
  python -m brief.crawler --from-json day.json --tournament-id 5766   # 用存檔測試（不連網）
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import sqlite3
import time
from pathlib import Path

try:
    import requests
except ImportError:  # 解析與寫入不需要 requests，只有連網時才需要
    requests = None

SITE = "https://bwfbadminton.com"
API = "https://extranet-lv.bwfbadminton.com/api"
HEADERS = {"User-Agent": "badminton-daily-agent/0.1 (personal research; contact via GitHub cxinyuan913-svg)"}
REQUEST_GAP_SEC = 2.0          # 每次請求至少間隔 2 秒
SCHEMA = Path(__file__).with_name("schema.sql")

FINISHED = {"F", "O"}          # F=Finished, O=Off court（已下場，比分已定）
MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], start=1)}
GUID_RE = re.compile(r"[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}", re.I)


# ---------------------------------------------------------------- HTTP
class Client:
    def __init__(self, gap: float = REQUEST_GAP_SEC):
        if requests is None:
            raise RuntimeError("需要先安裝 requests：pip install requests")
        self.s = requests.Session()
        self.s.headers.update(HEADERS)
        self.gap = gap
        self._last = 0.0

    def get(self, url: str, **params):
        wait = self.gap - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        for attempt in range(3):
            try:
                r = self.s.get(url, params=params or None, timeout=30)
                self._last = time.monotonic()
                if r.status_code == 404:
                    return None
                r.raise_for_status()
                return r
            except requests.RequestException:
                if attempt == 2:
                    raise
                time.sleep(5 * (attempt + 1))


# ---------------------------------------------------------------- 解析
def parse_tournament_page(tournament_id: int, page_html: str) -> dict:
    """從賽事頁 HTML 取出名稱、GUID、日期區間。"""
    title = re.search(r"<title>([^<]*)", page_html)
    name = html.unescape(title.group(1)).split("|", 1)[-1].strip() if title else ""
    code = GUID_RE.search(page_html)
    date_el = re.search(r'class="live-date">\s*([^<]+)<', page_html)
    year_m = re.search(r"(20\d{2})", name)
    start, end = parse_date_range(date_el.group(1) if date_el else "",
                                  int(year_m.group(1)) if year_m else None)
    return {
        "tournament_id": tournament_id,
        "code": code.group(0).upper() if code else None,
        "name": name,
        "start_date": start.isoformat() if start else None,
        "end_date": end.isoformat() if end else None,
        "source_url": f"{SITE}/tournament/{tournament_id}/x/",
    }


def parse_date_range(text: str, year: int | None):
    """'21 - 26 July' 或 '30 September - 04 October' → (date, date)。
    年份取自賽事名稱；跨年（12 月開打、1 月結束）時結束日加一年。"""
    if not text or not year:
        return None, None
    m = re.match(r"\s*(\d{1,2})\s*([A-Za-z]+)?\s*-\s*(\d{1,2})\s*([A-Za-z]+)", text)
    if not m:
        return None, None
    d1, mon1, d2, mon2 = m.groups()
    end_month = MONTHS[mon2.lower()]
    start_month = MONTHS[mon1.lower()] if mon1 else end_month
    start = dt.date(year, start_month, int(d1))
    end = dt.date(year + (1 if end_month < start_month else 0), end_month, int(d2))
    return start, end


def dates_between(start: str, end: str):
    d = dt.date.fromisoformat(start)
    stop = dt.date.fromisoformat(end)
    while d <= stop:
        yield d.isoformat()
        d += dt.timedelta(days=1)


# ---------------------------------------------------------------- 寫入
def is_finished(m: dict) -> bool:
    """已完成且有勝方。進行中的賽事 matchStatus 是 F/O；
    舊賽事（例如 2019 年）matchStatus 是 null，只能看 winner。"""
    status = m.get("matchStatus")
    return (status in FINISHED or status is None) and m.get("winner") in (1, 2)


def connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA.read_text(encoding="utf-8"))
    return con


def upsert_tournament(con, t: dict):
    con.execute(
        """INSERT INTO tournament (tournament_id, code, name, grade, level, start_date, end_date, source_url)
           VALUES (:tournament_id, :code, :name, :grade, :level, :start_date, :end_date, :source_url)
           ON CONFLICT(tournament_id) DO UPDATE SET
             code=excluded.code, name=excluded.name,
             grade=COALESCE(excluded.grade, tournament.grade),
             level=COALESCE(excluded.level, tournament.level),
             start_date=excluded.start_date, end_date=excluded.end_date,
             source_url=excluded.source_url, updated_at=datetime('now')""",
        {"grade": None, "level": None, **t},
    )


def upsert_player(con, p: dict, seen_date: str):
    con.execute(
        """INSERT INTO player (player_id, first_name, last_name, name_display, name_short,
                               country_code, slug, first_seen)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(player_id) DO UPDATE SET
             first_name=excluded.first_name, last_name=excluded.last_name,
             name_display=excluded.name_display, name_short=excluded.name_short,
             country_code=excluded.country_code, slug=excluded.slug,
             first_seen=MIN(COALESCE(player.first_seen, excluded.first_seen), excluded.first_seen),
             updated_at=datetime('now')""",
        (int(p["id"]), p.get("firstName"), p.get("lastName"), p.get("nameDisplay"),
         (p.get("nameShort") or "").strip(), p.get("countryCode"), p.get("slug"), seen_date),
    )


def pairing_id(con, player_ids: list[int]) -> int:
    ids = sorted(player_ids)
    a, b = ids[0], (ids[1] if len(ids) > 1 else None)
    if b is None:
        con.execute("INSERT OR IGNORE INTO pairing (player_a_id, player_b_id) VALUES (?, NULL)", (a,))
        row = con.execute("SELECT pairing_id FROM pairing WHERE player_a_id=? AND player_b_id IS NULL", (a,)).fetchone()
    else:
        con.execute("INSERT OR IGNORE INTO pairing (player_a_id, player_b_id) VALUES (?, ?)", (a, b))
        row = con.execute("SELECT pairing_id FROM pairing WHERE player_a_id=? AND player_b_id=?", (a, b)).fetchone()
    return row[0]


DISCIPLINE = {"Men's Singles": "MS", "Women's Singles": "WS", "Men's Doubles": "MD",
              "Women's Doubles": "WD", "Mixed Doubles": "XD"}


def _store_individual(con, tournament_id: int, m: dict, stats: dict,
                      event: str | None = None, team_tie_id: int | None = None) -> None:
    if not is_finished(m):
        stats["skipped_unfinished"] += 1
        return
    t1 = (m.get("team1") or {}).get("players") or []
    t2 = (m.get("team2") or {}).get("players") or []
    if not t1 or not t2:
        stats["skipped_no_players"] += 1    # 輪空等情況
        return
    match_date = (m.get("matchTime") or "")[:10]
    for p in t1 + t2:
        upsert_player(con, p, match_date)
    s1 = pairing_id(con, [int(p["id"]) for p in t1])
    s2 = pairing_id(con, [int(p["id"]) for p in t2])
    con.execute(
        """INSERT INTO match (match_id, tournament_id, event, round, match_date, match_time_utc,
                              duration_min, court, side1_id, side2_id, side1_seed, side2_seed,
                              winner_side, score_status, team_tie_id, rubber_no)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(match_id) DO UPDATE SET
             round=excluded.round, match_date=excluded.match_date,
             duration_min=excluded.duration_min, winner_side=excluded.winner_side,
             score_status=excluded.score_status, team_tie_id=excluded.team_tie_id,
             rubber_no=excluded.rubber_no, updated_at=datetime('now')""",
        (int(m["id"]), tournament_id, event or m.get("eventName"), m.get("roundName"), match_date,
         m.get("matchTimeUtc"), m.get("duration"), m.get("courtName"), s1, s2,
         m.get("team1seed"), m.get("team2seed"), m["winner"], m.get("scoreStatusValue"),
         team_tie_id, m.get("matchTypeNo") if team_tie_id else None),
    )
    con.execute("DELETE FROM game WHERE match_id=?", (int(m["id"]),))
    for g in m.get("score") or []:
        con.execute("INSERT INTO game (match_id, game_no, side1_points, side2_points) VALUES (?,?,?,?)",
                    (int(m["id"]), g["set"], g.get("home"), g.get("away")))
    stats["stored"] += 1


def _store_team_tie(con, tournament_id: int, m: dict, stats: dict) -> None:
    """團體賽：外層是國家對國家（比分如 3-0），單場在 m["matches"]。
    單場的 eventName 是賽事名（例如 Uber Cup），項目要從 matchTypeValue 轉成 MS/WS/…"""
    tie_id = int(m["id"])
    team_score = (m.get("score") or [{}])[0]
    con.execute(
        """INSERT INTO team_tie (team_tie_id, tournament_id, competition, stage, round, match_date,
                                 team1_country, team2_country, team1_score, team2_score, winner_side)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(team_tie_id) DO UPDATE SET
             team1_score=excluded.team1_score, team2_score=excluded.team2_score,
             winner_side=excluded.winner_side, updated_at=datetime('now')""",
        (tie_id, tournament_id, m.get("eventName"), m.get("drawName"), m.get("roundName"),
         (m.get("matchTime") or "")[:10], (m.get("team1") or {}).get("countryCode"),
         (m.get("team2") or {}).get("countryCode"), team_score.get("home"), team_score.get("away"),
         m["winner"] if m.get("winner") in (1, 2) else None),
    )
    stats["team_ties"] += 1
    for sub in m.get("matches") or []:
        _store_individual(con, tournament_id, sub, stats,
                          event=DISCIPLINE.get(sub.get("matchTypeValue"), sub.get("eventName")),
                          team_tie_id=tie_id)


def store_day(con, tournament_id: int, matches: list[dict]) -> dict:
    """寫入一天的比賽。只收已完成、有勝方的單場；團體賽的單場也會拆開寫入。回傳統計。"""
    stats = {"stored": 0, "skipped_unfinished": 0, "team_ties": 0, "skipped_no_players": 0}
    for m in matches:
        if m.get("isTeamMatch"):
            _store_team_tie(con, tournament_id, m, stats)
        else:
            _store_individual(con, tournament_id, m, stats)
    return stats


# ---------------------------------------------------------------- 流程
def crawl_tournament(client: Client, con, tournament_id: int, verbose=True) -> dict:
    r = client.get(f"{SITE}/tournament/{tournament_id}/x/")
    if r is None:
        raise ValueError(f"找不到賽事 {tournament_id}")
    t = parse_tournament_page(tournament_id, r.text)
    return crawl_known(client, con, t, verbose)


def crawl_known(client: Client, con, t: dict, verbose=True) -> dict:
    """已經知道 GUID 與日期時（例如來自 live.current_live），直接抓每日賽果。"""
    tournament_id = t["tournament_id"]
    if not (t["code"] and t["start_date"]):
        raise ValueError(f"賽事 {tournament_id} 缺少 GUID 或日期：{t}")
    upsert_tournament(con, t)
    total = {"stored": 0, "skipped_unfinished": 0, "team_ties": 0, "skipped_no_players": 0}
    for day in dates_between(t["start_date"], t["end_date"]):
        resp = client.get(f"{API}/tournaments/day-matches",
                          tournamentCode=t["code"], date=day, order=2, court=0)
        day_matches = resp.json() if resp is not None else []
        stats = store_day(con, tournament_id, day_matches)
        con.commit()
        for k in total:
            total[k] += stats[k]
        if verbose:
            print(f"  {day}: {len(day_matches)} 場，寫入 {stats['stored']}")
    if verbose:
        print(f"{t['name']}：共寫入 {total['stored']} 場；未完成 {total['skipped_unfinished']}")
    return {"tournament": t, **total}


def main():
    ap = argparse.ArgumentParser(description="BWF 賽果爬蟲")
    ap.add_argument("tournament_id", nargs="?", type=int)
    ap.add_argument("--db", default="brief.db")
    ap.add_argument("--from-json", help="用存好的 day-matches JSON 寫入（不連網）")
    ap.add_argument("--tournament-id", type=int, help="搭配 --from-json 使用")
    args = ap.parse_args()

    con = connect(args.db)
    if args.from_json:
        data = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
        tid = args.tournament_id or args.tournament_id
        upsert_tournament(con, {"tournament_id": tid, "code": data[0]["tournamentCode"] if data else None,
                                "name": data[0]["tournamentName"] if data else "", "start_date": None,
                                "end_date": None, "source_url": f"{SITE}/tournament/{tid}/x/"})
        print(store_day(con, tid, data))
        con.commit()
        return
    if not args.tournament_id:
        ap.error("請給賽事 ID，例如 python -m brief.crawler 5766")
    crawl_tournament(Client(), con, args.tournament_id)


if __name__ == "__main__":
    main()
