"""口播腳本的素材：從資料庫算出候選故事（交接單 003、notes 10:15／10:20）

每則故事：{kind, score, event, facts}；facts 是可驗證的中文句子（名字、國家、排名、比分都來自資料庫）。
LLM 只拿事實清單寫稿，不拿原始資料；事實檢查以事實清單為準。

故事類型：
  rank_gap       勝方排名比敗方差很多（例 #43 勝 #3），或這站一路打掉世界第一
  comeback       先輸一局後逆轉；第一局延長（≥ 22 分）加分
  h2h_dominance  交手紀錄懸殊（例 8–1，至少 5 場、弱勢方勝率 ≤ 20%）
  rematch        120 天內大賽決賽同組合、同結果（例 世錦賽決賽重演）
  first_title    資料庫範圍內首座同級冠軍
  run_summary    冠軍整站局數（例 全屆只丟 1 局）、唯一延長局、擊敗的最高排名對手
  upset          規則判定的爆冷（digest.upset_level）
  tpe_giant      中華台北選手擊敗排名高很多的對手
  extended       延長局（≥ 22 分）逆轉；只有局分資料，不推論賽末點數
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

from brief import digest, zh

DEEP = {"Final": "決賽", "F": "決賽", "SF": "四強"}
BIG_LEVELS = ("G1_IND", "G1_EVENT", "WTF", "S1000", "S750", "MULTI", "CONT_IND")
PLACE = {"W": "冠軍", "F": "亞軍", "SF": "四強", "QF": "八強", "R16": "16 強", "R32": "32 強", "R64": "64 強", "R128": "128 強"}
# 只有頒獎牌的賽事才寫金銀銅（2026-10-01 試寫發現：台北公開賽 S300 被寫成「混雙銀牌」「好幾面銅牌」）
MEDAL_LEVELS = {"G1_IND", "G1_EVENT", "MULTI", "MULTI_TEAM", "CONT_IND", "CONT_TEAM", "FISU", "G1_TEAM"}
MEDAL_PLACE = {"W": "冠軍（金牌）", "F": "亞軍（銀牌）", "SF": "四強（銅牌）"}


def place_name(level: str | None, pos: str) -> str:
    if level in MEDAL_LEVELS and pos in MEDAL_PLACE:
        return MEDAL_PLACE[pos]
    return PLACE.get(pos, pos)
EVENT_ZH = zh.EVENT


def _rk(r, src=None):
    """只用官方排名（notes 10:40）：估算、百名外（估算）、查不到都回傳 None，事實清單就不寫排名。"""
    if src == "official" and r:
        return f"世界 #{r}"
    return "百名外" if src == "outside100" else None


def official_only(m: dict) -> dict:
    """估算或查不到的排名清掉，後面的故事規則（排名差、爆冷巨人）就不會用到。"""
    for side in ("winner", "loser"):
        if m.get(f"{side}_rank_src") != "official":
            m[f"{side}_rank"] = None
    return m


def games(score: str) -> list[tuple[int, int]]:
    """勝方在前的比分字串 → [(勝方, 敗方), …]。"""
    out = []
    for g in (score or "").split():
        a, _, b = g.partition("-")
        if a.isdigit() and b.isdigit():
            out.append((int(a), int(b)))
    return out


def side_text(side: dict, rank: str | None = None) -> str:
    inner = zh.country(side["country"]) + (f"，{rank}" if rank else "")
    return f"{side['name']}（{inner}）"


def match_fact(m: dict) -> str:
    w, l = m["winner"], m["loser"]
    tag = f"，規則判定{m['upset']}" if m.get("upset") else ""
    return (f"{EVENT_ZH.get(m['event'], m['event'])} {zh.round_name(m['round'])}："
            f"{side_text(w, _rk(m['winner_rank'], m.get('winner_rank_src')))}勝 "
            f"{side_text(l, _rk(m['loser_rank'], m.get('loser_rank_src')))}，比分 {m['score']}{tag}")


def tournament_matches(con, tournament_id: int, upto: str) -> list[dict]:
    ids = [r[0] for r in con.execute("SELECT match_id FROM match WHERE tournament_id=? AND match_date<=? AND team_tie_id IS NULL "
                                     "AND winner_side IN (1, 2)", (tournament_id, upto))]
    return [official_only(m) for m in digest.stage_matches(con, tournament_id, ids)]


def h2h_record(con, p1: int, p2: int, upto: str) -> tuple[int, int, list]:
    rows = con.execute(
        """SELECT m.match_date, m.round, t.name, t.level,
                  CASE WHEN (m.side1_id=? AND m.winner_side=1) OR (m.side2_id=? AND m.winner_side=2) THEN 1 ELSE 0 END
           FROM match m JOIN tournament t USING (tournament_id)
           WHERE ((m.side1_id=? AND m.side2_id=?) OR (m.side1_id=? AND m.side2_id=?)) AND m.match_date<=? AND m.winner_side IN (1, 2)
           ORDER BY m.match_date""", (p1, p1, p1, p2, p2, p1, upto)).fetchall()
    w = sum(r[4] for r in rows)
    return w, len(rows) - w, rows


def placings(con, tournament_id: int) -> dict[tuple[str, int], str]:
    return {(ev, pid): pos for pid, ev, pos in con.execute(
        "SELECT pairing_id, event, round_reached FROM tournament_result WHERE tournament_id=?", (tournament_id,))}


# ---------------------------------------------------------------- 故事
def stories_for(con, t: dict, day: str, day_matches: list[dict], all_matches: list[dict]) -> list[dict]:
    """day_matches：當天的比賽；all_matches：整站到當天的比賽（跨天統計用）。"""
    out = []
    by_pid = defaultdict(list)
    for m in all_matches:
        by_pid[(m["event"], m["winner"]["pairing_id"])].append(m)
        by_pid[(m["event"], m["loser"]["pairing_id"])].append(m)
    for m in day_matches:
        w, l, wr, lr = m["winner"], m["loser"], m["winner_rank"], m["loser_rank"]
        ev = EVENT_ZH.get(m["event"], m["event"])
        g = games(m["score"])
        base = match_fact(m)
        if wr and lr and wr > lr and wr - lr >= 20:
            beaten_one = [x for x in by_pid[(m["event"], w["pairing_id"])]
                          if x["winner"]["pairing_id"] == w["pairing_id"] and x["loser_rank"] == 1 and x["match_id"] != m["match_id"]]
            facts = [base] + [match_fact(x) for x in beaten_one]
            out.append({"kind": "rank_gap", "score": min((wr - lr) / 5, 10) + (4 if beaten_one else 0) + (2 if m["round"] in DEEP else 0),
                        "event": m["event"], "facts": facts})
        elif m.get("winner_rank_src") == "outside100" and lr and lr <= 20:
            out.append({"kind": "rank_gap", "score": 5, "event": m["event"], "facts": [base]})
        if len(g) == 3 and g[0][0] < g[0][1]:
            ext = max(g[0]) >= 22
            out.append({"kind": "comeback", "score": 4 + (2 if ext else 0) + (2 if m["round"] in DEEP else 0),
                        "event": m["event"], "facts": [base, f"{w['name']}先輸第一局 {g[0][0]}-{g[0][1]}"
                                                       + ("（延長局）" if ext else "") + "，後兩局逆轉"]})
        for i, (a, b) in enumerate(g):
            if max(a, b) >= 22 and a > b and len(g) == 3 and i > 0:
                out.append({"kind": "extended", "score": 2, "event": m["event"],
                            "facts": [base, f"第 {i + 1} 局 {a}-{b} 延長拿下"]})
        if m.get("upset"):
            out.append({"kind": "upset", "score": 7 if m["upset"] == "大爆冷" else 5, "event": m["event"], "facts": [base]})
        if w["home"] and lr and ((wr is None and m.get("winner_rank_src") == "outside100") or (wr and wr - lr >= 15)):
            out.append({"kind": "tpe_giant", "score": 6, "event": m["event"], "facts": [base]})
        hw, hl, rows = h2h_record(con, w["pairing_id"], l["pairing_id"], m["date"])
        total = hw + hl
        if total >= 5 and min(hw, hl) / total <= 0.2:
            out.append({"kind": "h2h_dominance", "score": 3 + total / 4 + (2 if m["round"] in DEEP else 0), "event": m["event"],
                        "facts": [base, f"{w['name']}與{l['name']}交手紀錄 {hw} 勝 {hl} 負（資料庫 2017 年起）"]})
        if m["round"] in ("Final", "F"):
            cutoff = (dt.date.fromisoformat(m["date"]) - dt.timedelta(days=120)).isoformat()
            prev = [r for r in rows[:-1] if r[1] in ("Final", "F") and r[0] >= cutoff and r[3] in BIG_LEVELS and r[4] == 1]
            if prev:
                d, _, name, _, _ = prev[-1]
                out.append({"kind": "rematch", "score": 5, "event": m["event"],
                            "facts": [base, f"同一組合 {d[:7]} 在{zh.tournament(name)}決賽也是{w['name']}勝"]})
            titles = con.execute(
                """SELECT COUNT(*) FROM tournament_result r JOIN tournament t USING (tournament_id)
                   WHERE r.pairing_id=? AND r.event=? AND r.round_reached='W' AND t.level IS ? AND r.result_date < ?""",
                (w["pairing_id"], m["event"], t["level"], m["date"])).fetchone()[0]
            if titles == 0:
                out.append({"kind": "first_title", "score": 3, "event": m["event"],
                            "facts": [base, f"資料庫範圍內是{w['name']}第一座{zh.level(t['level'])}{ev}冠軍"]})
            run = [x for x in by_pid[(m["event"], w["pairing_id"])]]
            lost = sum(1 for x in run for a, b in games(x["score"]) if (x["winner"]["pairing_id"] == w["pairing_id"]) == (a < b))
            won = sum(1 for x in run for a, b in games(x["score"]) if (x["winner"]["pairing_id"] == w["pairing_id"]) == (a > b))
            ext = [x for x in run for a, b in games(x["score"]) if max(a, b) >= 22]
            best = min((x["loser_rank"] for x in run if x["winner"]["pairing_id"] == w["pairing_id"] and x["loser_rank"]), default=None)
            facts = [base, f"{w['name']}整站 {len(run)} 場、局數 {won} 勝 {lost} 負"]
            if best:
                facts.append(f"{w['name']}這站擊敗的最高排名對手是世界 #{best}")
            facts += [f"{zh.round_name(x['round'])} {x['score']}" for x in sorted(run, key=lambda x: x["date"])]
            out.append({"kind": "run_summary", "score": 8 if lost <= 1 else 3, "event": m["event"], "facts": facts})
    return sorted(out, key=lambda s: -s["score"])


# ---------------------------------------------------------------- 台灣段落
def taiwan_facts(con, t: dict, matches: list[dict], whole: bool) -> list[str]:
    """whole=True：整站（風格 3）；False：當天（每日台灣視角）。含「差一點」（輸的那局差 ≤ 2 分）與輸給後來的名次。"""
    place = placings(con, t["tournament_id"])
    out = []
    for m in sorted((x for x in matches if x["winner"]["home"] or x["loser"]["home"]), key=lambda x: (x["date"], x["match_id"])):
        line = match_fact(m)
        if m["loser"]["home"]:
            close = [f"{b}-{a}" for a, b in games(m["score"]) if a > b and a - b <= 2]
            if close:
                line += f"；台灣這邊輸的局差 2 分以內：{'、'.join(close)}（差一點）"
            opp = place.get((m["event"], m["winner"]["pairing_id"]))
            if opp in ("W", "F"):
                line += f"；對手最後拿到{place_name(t.get('level'), opp)}"
        out.append(line)
    if whole:
        seen = set()
        for m in matches:
            for side in (m["winner"], m["loser"]):
                key = (m["event"], side["pairing_id"])
                if side["home"] and key not in seen:
                    seen.add(key)
                    pos = place.get(key)
                    if pos:
                        out.append(f"台灣 {EVENT_ZH.get(m['event'], m['event'])} {side['name']} 本站最後名次：{place_name(t.get('level'), pos)}")
    return out


def champions(matches: list[dict], level: str | None = None) -> list[str]:
    label = place_name(level, "W")
    return [f"{EVENT_ZH.get(m['event'], m['event'])}{label}：{side_text(m['winner'])}"
            for m in matches if m["round"] in ("Final", "F")]
