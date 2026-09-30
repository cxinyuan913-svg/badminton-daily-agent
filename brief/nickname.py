"""選手暱稱自動收集（2026-09-30 Raymond 決議，見 docs/notes-from-claude-ai.md 17:45）

來源：每天收進來的羽球新聞（目前只有標題）。找候選的兩條路：
  1. 規則（可直接對回選手）：
     - 組合：「XY配」，X、Y 分別是兩位選手中文名裡的字（麟洋配 → 王齊麟、李洋）
     - 個人：「小X」「X神」，X 是選手中文名裡的字（只接受能唯一對到一位選手的）
  2. LLM（例行模型）：給新聞文字與選手名單，輸出「暱稱 → 選手」，**必須附原文句子當證據**；
     證據不在原文、暱稱不在證據裡、選手不在名單上的一律不收
狀態：
  candidate 剛發現
  auto      自動採用：同一篇裡暱稱與全名同時出現（至少一次），且 ≥ 2 個不同來源或累積 ≥ 3 次
  confirmed / rejected：Raymond 確認或否決過（程式不會改動）
用途：auto 與 confirmed 加進新聞篩選關鍵字（news.keywords）；日報提到選手時不用暱稱。
每週一在日報最後列出本週新增的 auto 與待確認的 candidate，每則一行＋證據句。
"""
from __future__ import annotations

import datetime as dt
import json
import re
from itertools import permutations

NICKNAME_TABLE = """
CREATE TABLE IF NOT EXISTS nickname (
    nickname        TEXT NOT NULL,
    target_zh       TEXT NOT NULL,                -- 對應的選手中文名，組合用「／」分隔，例如「王齊麟／李洋」
    player_ids      TEXT,                         -- 已知的 BWF 選手 ID，逗號分隔（退休選手可能沒有）
    status          TEXT NOT NULL DEFAULT 'candidate',   -- candidate / auto / confirmed / rejected
    evidence        TEXT,                         -- 第一次看到的證據句
    source_url      TEXT,
    first_seen      TEXT,
    last_seen       TEXT,
    occurrences     INTEGER NOT NULL DEFAULT 0,
    status_at       TEXT,                         -- 狀態最後一次變更的日期
    PRIMARY KEY (nickname, target_zh)
);
CREATE TABLE IF NOT EXISTS nickname_seen (
    nickname        TEXT NOT NULL,
    target_zh       TEXT NOT NULL,
    url             TEXT NOT NULL,
    source          TEXT NOT NULL,
    seen_date       TEXT NOT NULL,
    cooccur         INTEGER NOT NULL,             -- 同一篇也出現全名（中文名）
    evidence        TEXT NOT NULL,
    method          TEXT NOT NULL,                -- rule / llm
    PRIMARY KEY (nickname, target_zh, url)
);
"""

# 退休選手不在追蹤名單裡，但他們的暱稱仍會出現在新聞中（2026-09-30 Raymond：麟洋配照常保留）
RETIRED = {"戴資穎": None, "李洋": None}
SEED = [("麟洋配", ["王齊麟", "李洋"], "confirmed", "冷啟動：Raymond 確認（2026-09-30）")]

PAIR_RE = re.compile(r"([一-鿿])([一-鿿])配")
XIAO_RE = re.compile(r"小([一-鿿])")
SHEN_RE = re.compile(r"([一-鿿])神")

LLM_SYSTEM = """你會收到一則羽球新聞的文字和一份選手中文名單。找出文中用來指稱名單上選手（或兩人組合）的暱稱或綽號。
只輸出 JSON 陣列，每筆 {"nickname": "...", "players": ["名單上的中文名", ...], "evidence": "原文中包含這個暱稱的句子（逐字照抄）"}。
規則：只收名單上的選手；evidence 必須逐字出自原文；沒有把握就不要輸出；沒有暱稱就輸出 []。"""


def players(con=None) -> dict[str, str | None]:
    """{中文名: player_id}：名單（config/players_zh.csv 或暫用表）＋退休選手。"""
    from brief import zh
    names = {r["name_zh"]: r["player_id"] for r in zh.load_player_table() if r["name_zh"]}
    return {**RETIRED, **names}


def rule_candidates(text: str, names: dict[str, str | None]) -> list[tuple[str, list[str]]]:
    """回傳 [(暱稱, [中文名…])]，只收能唯一對回選手的。"""
    out = []
    for m in PAIR_RE.finditer(text):
        x, y = m.groups()
        hits = {tuple(sorted((a, b))) for a, b in permutations(names, 2) if x in a[1:] and y in b[1:]}
        if len(hits) == 1:
            out.append((m.group(0), list(next(iter(hits)))))
    for rx in (XIAO_RE, SHEN_RE):
        for m in rx.finditer(text):
            ch = m.group(1)
            hits = [n for n in names if ch in (n[0], n[-1])]    # 只認姓氏或名字最後一字，避免「天神」對到周天成
            if len(hits) == 1:
                out.append((m.group(0), hits))
    return out


def llm_candidates(llm, text: str, names: dict[str, str | None]) -> list[tuple[str, list[str], str]]:
    """LLM 擷取＋驗證：證據要逐字在原文、暱稱要在證據裡、選手要在名單上。"""
    if hasattr(llm, "task"):
        llm.task = "nickname"
    raw = llm.complete(LLM_SYSTEM, f"選手名單：{'、'.join(sorted(names))}\n\n新聞：{text}")
    try:
        items = json.loads(raw[raw.find("["): raw.rfind("]") + 1])
    except (ValueError, TypeError):
        return []
    out = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        nick, who, ev = it.get("nickname"), it.get("players") or [], it.get("evidence") or ""
        if (isinstance(nick, str) and nick and ev and ev in text and nick in ev and who
                and all(w in names for w in who) and nick not in who):
            out.append((nick, sorted(who), ev))
    return out


def _target(who: list[str]) -> str:
    return "／".join(sorted(who))


def seed(con, today: dt.date) -> None:
    con.executescript(NICKNAME_TABLE)
    for nick, who, status, ev in SEED:
        target = _target(who)                    # 與 scan 同一種排序，才會對到同一筆
        con.execute("""INSERT OR IGNORE INTO nickname (nickname, target_zh, status, evidence, first_seen, last_seen, status_at)
                       VALUES (?,?,?,?,?,?,?)""", (nick, target, status, ev, today.isoformat(), today.isoformat(),
                                                   today.isoformat()))
    con.commit()


def scan(con, items: list[dict], today: dt.date, llm=None) -> int:
    """掃描新聞（{source, url, title, published}），記錄出現並更新狀態。回傳新記錄的出現次數。冪等。"""
    seed(con, today)
    names = players(con)
    added = 0
    for it in items:
        text = it["title"] + ("\n" + it["summary"] if it.get("summary") else "")
        found = [(n, w, it["title"], "rule") for n, w in rule_candidates(text, names)]
        if llm is not None:
            try:
                found += [(n, w, ev, "llm") for n, w, ev in llm_candidates(llm, text, names)]
            except Exception:  # noqa: BLE001 — LLM 失敗不影響規則擷取
                pass
        for nick, who, ev, method in found:
            cooccur = int(any(w in text.replace(nick, "") for w in who))
            added += con.execute(
                """INSERT OR IGNORE INTO nickname_seen (nickname, target_zh, url, source, seen_date, cooccur, evidence, method)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (nick, _target(who), it["url"], it["source"], (it.get("published") or today.isoformat())[:10],
                 cooccur, ev, method)).rowcount
            ids = ",".join(sorted(str(names[w]) for w in who if names.get(w))) or None
            con.execute("""INSERT OR IGNORE INTO nickname (nickname, target_zh, player_ids, evidence, source_url, first_seen, status_at)
                           VALUES (?,?,?,?,?,?,?)""",
                        (nick, _target(who), ids, ev, it["url"], (it.get("published") or today.isoformat())[:10],
                         today.isoformat()))
    refresh(con, today)
    return added


def refresh(con, today: dt.date) -> None:
    """依出現紀錄重算次數與 candidate / auto；confirmed / rejected 不動。"""
    rows = con.execute(
        """SELECT nickname, target_zh, COUNT(*), COUNT(DISTINCT source), MAX(cooccur), MIN(seen_date), MAX(seen_date)
           FROM nickname_seen GROUP BY nickname, target_zh""").fetchall()
    for nick, target, n, sources, cooccur, first, last in rows:
        status = "auto" if cooccur and (sources >= 2 or n >= 3) else "candidate"
        con.execute(
            """UPDATE nickname SET occurrences=?, first_seen=MIN(COALESCE(first_seen, ?), ?), last_seen=?,
                 status_at=CASE WHEN status != ? AND status IN ('candidate', 'auto') THEN ? ELSE status_at END,
                 status=CASE WHEN status IN ('candidate', 'auto') THEN ? ELSE status END
               WHERE nickname=? AND target_zh=?""",
            (n, first, first, last, status, today.isoformat(), status, nick, target))
    con.commit()


def weekly_lines(con, today: dt.date) -> list[str]:
    """週一日報最後的暱稱週報：本週新增的 auto 與待確認的 candidate，每則一行＋證據句。"""
    if today.weekday() != 0:
        return []
    try:
        rows = con.execute(
            """SELECT nickname, target_zh, status, evidence, source_url, occurrences FROM nickname
               WHERE (status='auto' AND status_at > ?) OR status='candidate'
               ORDER BY status, occurrences DESC, nickname""",
            ((today - dt.timedelta(days=7)).isoformat(),)).fetchall()
    except Exception:  # noqa: BLE001
        return []
    if not rows:
        return []
    lines = ["", "__**暱稱週報**__（回覆「確認／否決 暱稱」即可；日報不會用暱稱稱呼選手）"]
    for nick, target, status, ev, url, n in rows:
        label = "自動採用" if status == "auto" else "待確認"
        lines.append(f"- 【{label}】{nick} → {target}（{n} 次）｜證據：{ev}" + (f" <{url}>" if url else ""))
    return lines


SCANNED_TABLE = "CREATE TABLE IF NOT EXISTS nickname_scanned (url TEXT PRIMARY KEY, scanned_at TEXT NOT NULL);"
ZH_SOURCES = ("cna", "nownews")          # BWF 是英文標題，不會有中文暱稱


def scan_new(con, today: dt.date, llm=None) -> int:
    """掃描還沒掃過的中文新聞（每則只掃一次，避免重複呼叫 LLM）。"""
    con.executescript(SCANNED_TABLE)
    try:
        rows = con.execute(
            f"""SELECT source, url, title, published FROM news_item
                WHERE source IN ({",".join("?" * len(ZH_SOURCES))}) AND url NOT IN (SELECT url FROM nickname_scanned)""",
            ZH_SOURCES).fetchall()
    except Exception:  # noqa: BLE001 — 還沒有 news_item
        return 0
    items = [dict(zip(("source", "url", "title", "published"), r)) for r in rows]
    n = scan(con, items, today, llm)
    con.executemany("INSERT OR IGNORE INTO nickname_scanned VALUES (?, ?)", [(i["url"], today.isoformat()) for i in items])
    con.commit()
    return n
