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


# ---------------------------------------------------------------- 故事候選（notes 15:40：資料層，不動提示詞）
# 交手紀錄從資料庫算（2017 年起的回補範圍），facts 一律寫明「2017 年以來」；排名紀錄只用官方排名（2019-01-15 起完整）
SINCE = "2017 年以來"
FINAL_ROUNDS = ("Final", "F")
DEEP_ROUNDS = ("Final", "F", "SF", "Semi-finals")


def h2h_detail(con, a: int, b: int, upto: str) -> list[dict]:
    """a 對 b 的每一場（含團體賽單場），依日期排序；a_won 與局分都是 a 的角度。"""
    rows = con.execute(
        """SELECT m.match_id, m.match_date, m.round, t.name, t.level, m.side1_id, m.winner_side, m.duration_min,
                  m.score_status, m.tournament_id
           FROM match m JOIN tournament t USING (tournament_id)
           WHERE ((m.side1_id=? AND m.side2_id=?) OR (m.side1_id=? AND m.side2_id=?))
             AND m.match_date<=? AND m.winner_side IN (1, 2)
           ORDER BY m.match_date, m.match_id""", (a, b, b, a, upto)).fetchall()
    out = []
    for mid, date, rnd, tname, level, s1, ws, dur, status, tid in rows:
        a_is_1 = s1 == a
        g = con.execute("SELECT side1_points, side2_points FROM game WHERE match_id=? ORDER BY game_no", (mid,)).fetchall()
        g = [(p1, p2) if a_is_1 else (p2, p1) for p1, p2 in g if p1 is not None and p2 is not None]
        out.append({"match_id": mid, "date": date, "round": rnd, "tournament": zh.tournament(tname), "level": level,
                    "tournament_id": tid, "a_won": (ws == 1) == a_is_1, "games": g, "duration": dur, "status": status})
    return out


def _score_a(gs: list[tuple[int, int]]) -> str:
    return " ".join(f"{x}-{y}" for x, y in gs)


def _record(ms: list[dict]) -> tuple[int, int]:
    w = sum(1 for m in ms if m["a_won"])
    return w, len(ms) - w


def turning_point(ms: list[dict], min_seg: int = 3) -> tuple[int, float] | None:
    """勝率前後落差最大的切點 k（前 k 場 vs 之後），兩段都至少 min_seg 場；落差 < 0.5 不算翻轉。"""
    best = None
    for k in range(min_seg, len(ms) - min_seg + 1):
        before = sum(m["a_won"] for m in ms[:k]) / k
        after = sum(m["a_won"] for m in ms[k:]) / (len(ms) - k)
        gap = abs(after - before)
        if best is None or gap > best[1]:
            best = (k, gap)
    return best if best and best[1] >= 0.5 else None


def streak(ms: list[dict]) -> tuple[bool, int]:
    """最近的連勝（True）或連敗（False）場數。"""
    if not ms:
        return True, 0
    last, n = ms[-1]["a_won"], 0
    for m in reversed(ms):
        if m["a_won"] != last:
            break
        n += 1
    return last, n


def _meet(m: dict) -> str:
    """賽事名本身帶年份（zh.tournament），不再重複寫日期。"""
    return f"{m['tournament']}{zh.round_name(m['round'])}"


def candidates_for_match(con, m: dict) -> list[dict]:
    """一場比賽可以延伸的故事候選：rivalry、domination、revenge、stuck_round、retired。
    a = 這場的勝方、b = 敗方；所有 facts 都能回資料庫查。"""
    w, l = m["winner"], m["loser"]
    wn, ln = w["name"], l["name"]
    base = match_fact(m)
    ms = h2h_detail(con, w["pairing_id"], l["pairing_id"], m["date"])
    out = []
    n = len(ms)
    deep = 2 if m["round"] in DEEP_ROUNDS else 0
    tw, tl = _record(ms)
    if n >= 8:
        facts = [base, f"{wn}對{ln}交手紀錄 {tw} 勝 {tl} 負（{SINCE}，共 {n} 場）"]
        tp = turning_point(ms)
        if tp:
            k = tp[0]
            bw, bl = _record(ms[:k])
            aw, al = _record(ms[k:])
            facts.append(f"前 {k} 場{wn} {bw} 勝 {bl} 負；從 {_meet(ms[k])}起 {aw} 勝 {al} 負")
        won, s = streak(ms)
        if s >= 2:
            facts.append(f"{wn}目前對{ln}{'連勝' if won else '連敗'} {s} 場")
        finals = [x for x in ms if x["round"] in FINAL_ROUNDS]
        if finals:
            fw, fl = _record(finals)
            facts.append(f"兩邊在決賽碰過 {len(finals)} 次，{wn} {fw} 勝 {fl} 負")
        longest = max((x for x in ms if x["duration"]), key=lambda x: x["duration"], default=None)
        if longest:
            facts.append(f"兩邊打最久的一場：{_meet(longest)}，{longest['duration']} 分鐘，"
                         f"{wn if longest['a_won'] else ln}勝（{wn}角度比分 {_score_a(longest['games'])}）")
        out.append({"kind": "rivalry", "score": 3 + n / 4 + (4 if tp else 0) + len(finals) / 2 + deep,
                    "event": m["event"], "facts": facts})
    if n >= 5 and tl == 0:
        g_all = [g for x in ms for g in x["games"]]
        won_g = sum(1 for x, y in g_all if x > y)
        lost_g = len(g_all) - won_g
        facts = [base, f"{wn}對{ln} {n} 戰全勝（{SINCE}），局數 {won_g} 勝 {lost_g} 負"]
        lost_by_l = [(x, y) for x, y in g_all if x > y]
        if lost_by_l:
            cx, cy = min(lost_by_l, key=lambda g: g[0] - g[1])
            facts.append(f"{ln}輸的局裡最接近的一局：{cy}-{cx}（{ln}角度）")
        out.append({"kind": "domination", "score": 4 + n / 2 + deep, "event": m["event"], "facts": facts})
    prev = ms[:-1]
    if prev and not prev[-1]["a_won"] and prev[-1]["round"] in DEEP_ROUNDS:
        p = prev[-1]
        out.append({"kind": "revenge", "score": 5 + (2 if p["round"] in FINAL_ROUNDS else 0) + deep, "event": m["event"],
                    "facts": [base, f"兩邊上次交手是 {_meet(p)}，{ln}勝（{wn}角度比分 {_score_a(p['games'])}）"]})
    beaten = [x for x in ms if x["a_won"]]           # 敗方輸給勝方的場次
    if len(beaten) >= 4:
        rounds: dict[str, int] = defaultdict(int)
        for x in beaten:
            rounds[x["round"]] += 1
        rnd, cnt = max(rounds.items(), key=lambda kv: kv[1])
        if cnt >= 3 and cnt / len(beaten) >= 0.5:
            out.append({"kind": "stuck_round", "score": 3 + cnt + (2 if rnd == m["round"] else 0), "event": m["event"],
                        "facts": [base, f"{ln}輸給{wn}的 {len(beaten)} 場裡，有 {cnt} 場在{zh.round_name(rnd)}（{SINCE}）"]})
    status = con.execute("SELECT score_status FROM match WHERE match_id=?", (m["match_id"],)).fetchone()
    if status and status[0] in ("Retired", "Walkover") and m["round"] in ("QF", "SF", "Final", "F"):
        word = "中途退賽" if status[0] == "Retired" else "賽前退賽（不戰而勝）"
        out.append({"kind": "retired", "score": 4 + (3 if m["round"] in FINAL_ROUNDS else 0), "event": m["event"],
                    "facts": [base, f"這場{ln}{word}"]})
    return out


def _champions_ranked_below(con, level: str, event: str, rank: int, before: str) -> tuple[int, int]:
    """2019-01-15 起、同層級同項目的冠軍：(官方排名比 rank 更低的人數, 有官方排名的冠軍總數)。"""
    from brief.rankings import rank_lookup
    rows = con.execute(
        """SELECT m.match_date, CASE m.winner_side WHEN 1 THEN m.side1_id ELSE m.side2_id END
           FROM match m JOIN tournament t USING (tournament_id)
           WHERE t.level=? AND m.event=? AND m.round IN ('Final', 'F') AND m.winner_side IN (1, 2)
             AND m.match_date >= '2019-01-15' AND m.match_date < ? AND m.team_tie_id IS NULL""",
        (level, event, before)).fetchall()
    lower = total = 0
    for date, pid in rows:
        r, src = rank_lookup(con, pid, event, date)
        if src == "official" and r:
            total += 1
            lower += r > rank
    return lower, total


def tournament_records(con, t: dict, all_matches: list[dict]) -> list[dict]:
    """整站的紀錄型故事：冠軍整站一局未失、同級賽事排名最低的冠軍（官方排名）、本站最長比賽。"""
    out = []
    for f in (m for m in all_matches if m["round"] in FINAL_ROUNDS):
        pid, ev = f["winner"]["pairing_id"], EVENT_ZH.get(f["event"], f["event"])
        run = [m for m in all_matches if m["event"] == f["event"] and pid in (m["winner"]["pairing_id"], m["loser"]["pairing_id"])]
        lost = sum(1 for m in run for a, b in games(m["score"]) if (m["winner"]["pairing_id"] == pid) == (a < b))
        if lost == 0 and len(run) >= 3:
            out.append({"kind": "record", "score": 6, "event": f["event"],
                        "facts": [match_fact(f), f"{f['winner']['name']}整站 {len(run)} 場一局未失"]})
        wr = f["winner_rank"]
        if wr and f.get("winner_rank_src") == "official":
            lower, total = _champions_ranked_below(con, t["level"], f["event"], wr, f["date"])
            if lower == 0 and total >= 5:
                out.append({"kind": "record", "score": 7, "event": f["event"],
                            "facts": [match_fact(f), f"世界 #{wr} 奪冠：2019 年以來{zh.level(t['level'])}{ev}的 {total} 位冠軍"
                                      f"（以官方排名計）沒有人排名比這更低"]})
    timed = []
    for m in all_matches:
        d = con.execute("SELECT duration_min FROM match WHERE match_id=?", (m["match_id"],)).fetchone()[0]
        if d:
            timed.append((d, m))
    if timed:
        d, m = max(timed, key=lambda x: x[0])
        out.append({"kind": "record", "score": 3 + (2 if d >= 90 else 0), "event": m["event"],
                    "facts": [match_fact(m), f"這是本站打最久的一場：{d} 分鐘"]})
    return out


def story_candidates(con, t: dict, matches: list[dict], all_matches: list[dict], with_records: bool = True) -> list[dict]:
    """matches：要找故事的場次（每日＝當天；整站＝全部）。分數高的在前；同一場同一類只留一則。"""
    out = []
    for m in matches:
        out += candidates_for_match(con, m)
    if with_records:
        out += tournament_records(con, t, all_matches)
    seen, uniq = set(), []
    for c in sorted(out, key=lambda c: -c["score"]):
        key = (c["kind"], c["facts"][0])
        if key not in seen:
            seen.add(key)
            uniq.append(c)
    return uniq
