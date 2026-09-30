"""世界排名積分規則表：(比賽日期, 層級, 打到的名次) → 積分（交接單 002 第 2 步）

名次欄位順序：冠、亞、3/4、5/8、9/16、17/32、33/64、65/128、129/256、257/512、513/1024
表中沒有的名次視為 0 分。

出處（皆於 2026-09-30 取得，遵守 robots.txt）：
  PRE2018  BWF GCR Part III Section 1A Appendix 6（Superseries／Grand Prix 舊制）
           https://system.bwfbadminton.com/documents/folder_1_9/folder_1_22/folder_1_30/GCR%20Appendix%206%20-%20World%20Ranking%20System.pdf
  V2018    BWF Statutes 5.3.3.1 World Ranking System, In Force 30/11/2018, 第 6.3 條
           https://fedebadchile.cl/wp-content/uploads/2019/05/3.3.3.1-World-Ranking-System-Nov2018-1.pdf
  V2024W17 BWF 新聞 2024-02-05「More Points on Offer at Top-Tier Tournaments」：
           Grade 1 冠軍 14500、WTF 14000、Super 1000 依加碼獎金 13500／12700（亞軍 10800）、Super 750 以下不變。
  V6.0     BWF Statutes 5.3.3.1 V6.0（In Force 2026-04-26），Raymond 手動下載到 docs/regulations/（不進版控）。
           §6.3 全表（含 12700 級所有名次、Grade 1／Level 1／Level 2 延伸到 513–1024）、§6.3 註（奧運季殿軍、
           年終總決賽小組第 3／4）、§6.5.1（洲際錦標賽比照等級）。

尚未處理（見 docs/status.md）：團體賽（規章 7.x 用平均分公式計算，需要排名）、大英國協運動會、
Super 1000 各站屬於哪一級。
"""
from __future__ import annotations

import csv
import datetime as dt
import re
from functools import lru_cache
from pathlib import Path

POSITIONS = ["W", "F", "SF", "QF", "R16", "R32", "R64", "R128", "R256", "R512", "R1024"]
WTF_GROUP = {"G3": 3, "G4": 4}      # 年終總決賽小組第 3、第 4（§4.2.8：5–6、7–8 名）

PRE2018 = {
    "G1_IND": [12000, 10200, 8400, 6600, 4800, 3000, 1200, 600, 240, 120, 60],   # 世錦賽、奧運
    "SSP": [11000, 9350, 7700, 6050, 4320, 2660, 1060, 520],                    # 含 Superseries Finals
    "SS": [9200, 7800, 6420, 5040, 3600, 2220, 880, 430],
    "GPG": [7000, 5950, 4900, 3850, 2750, 1670, 660, 320, 130, 60, 30],
    "GP": [5500, 4680, 3850, 3030, 2110, 1290, 510, 240, 100, 45, 30],
    "IC": [4000, 3400, 2800, 2200, 1520, 920, 360, 170, 70, 30, 20],
    "IS": [2500, 2130, 1750, 1370, 920, 550, 210, 100, 40, 20, 10],
    "FS": [1700, 1420, 1170, 920, 600, 350, 130, 60, 20, 10, 5],
}
PRE2018_OLYMPIC_3RD, PRE2018_OLYMPIC_4TH = 9200, 8400

V2018 = {
    "G1_IND": [13000, 11000, 9200, 7200, 5200, 3200, 1300, 650, 260, 130, 65],
    "WTF": [12000, 10200, 8400, 6600, 4800, 3000, 1200, 600, 240, 120, 60],     # Level 1
    "S1000": [12000, 10200, 8400, 6600, 4800, 3000, 1200, 600, 240, 120, 60],   # Level 2
    "S750": [11000, 9350, 7700, 6050, 4320, 2660, 1060, 520, 210, 100, 50],
    "S500": [9200, 7800, 6420, 5040, 3600, 2220, 880, 430, 170, 80, 40],
    "S300": [7000, 5950, 4900, 3850, 2750, 1670, 660, 320, 130, 60, 30],
    "S100": [5500, 4680, 3850, 3030, 2110, 1290, 510, 240, 100, 45, 30],
    "IC": PRE2018["IC"],
    "IS": PRE2018["IS"],
    "FS": PRE2018["FS"],
}
V2018_OLYMPIC_3RD, V2018_OLYMPIC_4TH = 10100, 9200

# 5.3.3.1 V6.0（2026-04-26）§6.3 全表；2024 第 17 週的調整只改 Grade 1、Level 1、Level 2（BWF 2024-02-05 公告），
# V6.0 表上這三列的數字與公告一致，所以整張表從 2024 第 17 週起適用
V2024W17 = {
    **V2018,
    "G1_IND": [14500, 12500, 10500, 8200, 6000, 3700, 1450, 750, 300, 150, 80],
    "WTF": [14000, 12000, 10000, 7800, 5700, 3500, 1400, 720, 280, 140, 75],
    "S1000": [13500, 11500, 9500, 7400, 5400, 3300, 1350, 670, 270, 135, 70],   # Level 2，加碼 ≥ US$500,000
    "S1000_12700": [12700, 10800, 9000, 7000, 5100, 3150, 1270, 630, 250, 125, 65],   # 加碼 US$250,000–499,999
    "L2_BASE": [12000, 10200, 8400, 6600, 4800, 3000, 1200, 600, 240, 120, 60],       # Level 2 無加碼（亞錦賽比照這列）
}
V2024W17_OLYMPIC_3RD, V2024W17_OLYMPIC_4TH = 11500, 10500     # §6.3 註 *
WTF_GROUP_3RD, WTF_GROUP_4TH = 8900, 7800                    # §6.3 註 **、§4.2.8（年終總決賽小組第 3、第 4）

VERSIONS = [  # (生效日, 名稱, 表)
    (dt.date(2018, 1, 1), "V2018", V2018),         # World Tour 自 2018 年開始；規章文件標示 2018-11-30 生效，積分表沿用
    (dt.date(2024, 4, 22), "V2024W17", V2024W17),  # 2024 第 17 週（週一 4/22）起開打的賽事
]

# 洲際個人錦標賽與洲際綜合運動會個人賽的比照等級（PRE2018：GCR 6.6.1；V2018：5.3.3.1 6.5.1）
CONTINENT_LEVEL = {
    "PRE2018": {"asia": "SS", "europe": "GPG", "oceania": "GP", "panam": "GP", "africa": "IC"},
    "V2018": {"asia": "S500", "europe": "S300", "oceania": "S100", "panam": "S100", "africa": "IC"},
    # V6.0 §6.5.1：亞洲 = Level 2、歐洲 = Level 4、泛美 = Level 5、大洋洲與非洲 = IC。
    # 何時從 2018 版改成這樣規章沒寫；用官方排名驗證後從 2024 第 17 週起適用（見 docs/ranking-validation.md）
    "V2024W17": {"asia": "L2_BASE", "europe": "S500", "oceania": "IC", "panam": "S300", "africa": "IC"},
}
CONTINENT_WORDS = [("asia", ("asia", "asian")), ("europe", ("europe", "european")),
                   ("oceania", ("oceania",)), ("panam", ("pan am", "pan-am", "panam", "pan american")),
                   ("africa", ("africa", "african"))]

# 2017 年以前的層級名稱在 calendar 叫 SSP/SS/GPG/GP；G1_EVENT 是舊分類下的世錦賽／奧運
LEVEL_ALIAS = {"G1_EVENT": "G1_IND"}


def version(on: dt.date) -> tuple[str, dict]:
    name, table = "PRE2018", PRE2018
    for start, n, t in VERSIONS:
        if on >= start:
            name, table = n, t
    return name, table


def continent(name: str) -> str | None:
    low = name.lower()
    for key, words in CONTINENT_WORDS:
        if any(w in low for w in words):
            return key
    return None


def effective_level(level: str | None, tournament_name: str, on: dt.date) -> str | None:
    """洲際錦標賽、洲際綜合運動會換算成比照的層級；查不到規則時回傳 None（不計分，而不是猜）。"""
    if level == "G1_EVENT" and "superseries finals" in tournament_name.lower():
        return "SSP"                            # 舊制年終總決賽與 Superseries Premier 同一列（GCR 6.3）
    level = LEVEL_ALIAS.get(level, level)
    if level == "S1000" and version(on)[0] == "V2024W17":
        return s1000_level(tournament_name, on, _cached_grades())
    if level in ("CONT_IND", "MULTI"):
        c = continent(tournament_name)
        rules = CONTINENT_LEVEL[version(on)[0]]
        return rules.get(c) if c else None      # 大英國協運動會不屬於任何洲 → None
    return level


def points(on: dt.date, level: str | None, position: str, tournament_name: str = "",
           olympic_place: int | None = None) -> int | None:
    """回傳積分。層級不計分或名次超出表格回傳 0；規則未知（例如 12700 級的非冠亞軍名次）回傳 None。
    olympic_place：奧運銅牌戰結果（3 或 4），奧運四強有不同積分。"""
    if position in WTF_GROUP:
        if version(on)[0] == "V2024W17":
            return WTF_GROUP_3RD if position == "G3" else WTF_GROUP_4TH
        position = "QF"                         # 舊版只寫「比照淘汰賽同名次」：5–8 名 = 八強那一列
    if position not in POSITIONS:
        raise ValueError(f"未知名次：{position}")
    lv = effective_level(level, tournament_name, on)
    if lv is None or lv in ("G1_TEAM", "MULTI_TEAM", "CONT_TEAM", "FISU"):
        return None if lv is None else 0
    name, table = version(on)
    row = table.get(lv)
    if row is None:
        return 0
    if olympic_place and position == "SF" and "olympic" in tournament_name.lower():
        third, fourth = {"PRE2018": (PRE2018_OLYMPIC_3RD, PRE2018_OLYMPIC_4TH),
                         "V2018": (V2018_OLYMPIC_3RD, V2018_OLYMPIC_4TH),
                         "V2024W17": (V2024W17_OLYMPIC_3RD, V2024W17_OLYMPIC_4TH)}.get(name, (None, None))
        if third:
            return third if olympic_place == 3 else fourth
    i = POSITIONS.index(position)
    if i < len(row):
        return row[i]
    return 0


# ---------------------------------------------------------------- 同一賽事的不同屆次
_SPONSOR_OR_YEAR = re.compile(r"\b(?:19|20)\d{2}\b|\([^)]*\)|\b\d+(?:st|nd|rd|th)\b|\b[IVXLC]+\b")


def series_key(name: str) -> str:
    """賽事的「同一站」鍵：去掉年份、括號、屆次與全大寫的贊助商字（VICTOR、PETRONAS、HSBC…）。
    例：「VICTOR China Open 2026」「VICTOR China Open 2025」→「china open」。用於 §2.2 與 Super 1000 分級。"""
    s = _SPONSOR_OR_YEAR.sub(" ", name)
    words = [w for w in re.split(r"[\s\-–]+", s) if w and not (w.isupper() and len(w) >= 2) and w.lower() != "badminton"]
    return " ".join(w.lower() for w in words)


# ---------------------------------------------------------------- Super 1000 分級（V6.0 §6.3）
S1000_GRADE_CSV = Path(__file__).resolve().parent.parent / "config" / "s1000_grade.csv"
S1000_LEVEL = {"13500": "S1000", "12700": "S1000_12700", "12000": "L2_BASE"}


def s1000_grades(path: Path = S1000_GRADE_CSV) -> dict[tuple[str, int], str]:
    """{(series_key, 年): 分級}；檔案由 brief/s1000_grade.py 以官方排名反推產生。"""
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig") as f:
        return {(series_key(r["name"]), int(r["year"])): r["grade"] for r in csv.DictReader(f)}


def s1000_level(name: str, on: dt.date, grades: dict | None = None) -> str:
    """2024 第 17 週起 Super 1000 依加碼獎金分三級；查不到用同一站最近一年的結果，再查不到用 13500。"""
    grades = s1000_grades() if grades is None else grades
    key = series_key(name)
    exact = grades.get((key, on.year))
    if exact is None:
        years = sorted((y for (k, y) in grades if k == key), key=lambda y: abs(y - on.year))
        exact = grades.get((key, years[0])) if years else None
    return S1000_LEVEL.get(exact or "13500", "S1000")


@lru_cache(maxsize=1)
def _cached_grades() -> dict:
    return s1000_grades()
