"""日報中文化（繁體中文、台灣用語）：項目、輪次、狀態、國家、賽事名稱、選手中文名。

原則（2026-09-30 Raymond 決議，見 docs/notes-from-claude-ai.md 17:05）：
  - 對照表查不到就保留原文，不自行翻譯
  - 選手名字**不音譯**：只用 player_zh.csv 裡人工確認過的中文名，格式「中文（英文）」
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

PLAYER_ZH_CSV = Path(__file__).with_name("player_zh.csv")           # 暫用：正式名單出現前
OFFICIAL_CSV = Path(__file__).resolve().parent.parent / "config" / "players_zh.csv"   # Raymond 維護的正式名單

EVENT = {"MS": "男單", "WS": "女單", "MD": "男雙", "WD": "女雙", "XD": "混雙"}

ROUND = {"Final": "決賽", "F": "決賽", "SF": "四強", "Semi-finals": "四強", "3/4": "銅牌戰", "QF": "八強", "R16": "16 強", "R32": "32 強",
         "R64": "64 強", "R128": "128 強"}

STATUS = {"Retired": "退賽", "Walkover": "不戰而勝", "Disqualified": "取消資格"}

COMPETITION = {"Thomas Cup": "湯姆斯盃", "Uber Cup": "尤伯盃", "Sudirman Cup": "蘇迪曼盃"}

LEVEL = {"G1_IND": "Grade 1", "G1_TEAM": "Grade 1 團體", "G1_EVENT": "Grade 1", "WTF": "年終總決賽",
         "S1000": "超級 1000", "S750": "超級 750", "S500": "超級 500", "S300": "超級 300", "S100": "超級 100",
         "SSP": "超級系列賽頂級", "SS": "超級系列賽", "GPG": "黃金大獎賽", "GP": "大獎賽",
         "IC": "國際挑戰賽", "IS": "國際系列賽", "FS": "未來系列賽", "CONT_IND": "洲際錦標賽", "CONT_TEAM": "洲際團體錦標賽", "MULTI": "綜合運動會",
         "MULTI_TEAM": "綜合運動會團體"}

# IOC／BWF 國家代碼 → 台灣慣用名稱
COUNTRY = {
    "TPE": "中華台北", "CHN": "中國", "HKG": "香港", "MAC": "澳門", "JPN": "日本", "KOR": "韓國", "PRK": "北韓",
    "INA": "印尼", "MAS": "馬來西亞", "SGP": "新加坡", "THA": "泰國", "VIE": "越南", "PHI": "菲律賓",
    "MYA": "緬甸", "CAM": "柬埔寨", "LAO": "寮國", "BRU": "汶萊", "IND": "印度", "SRI": "斯里蘭卡",
    "NEP": "尼泊爾", "PAK": "巴基斯坦", "BAN": "孟加拉", "MDV": "馬爾地夫", "MGL": "蒙古",
    "KAZ": "哈薩克", "UZB": "烏茲別克", "KGZ": "吉爾吉斯", "UAE": "阿聯", "IRI": "伊朗", "ISR": "以色列",
    "JOR": "約旦", "LBN": "黎巴嫩", "KSA": "沙烏地阿拉伯", "QAT": "卡達", "KUW": "科威特", "TUR": "土耳其",
    "DEN": "丹麥", "ENG": "英格蘭", "SCO": "蘇格蘭", "WAL": "威爾斯", "IRL": "愛爾蘭", "GBR": "英國",
    "FRA": "法國", "GER": "德國", "NED": "荷蘭", "BEL": "比利時", "ESP": "西班牙", "POR": "葡萄牙",
    "ITA": "義大利", "SUI": "瑞士", "AUT": "奧地利", "SWE": "瑞典", "NOR": "挪威", "FIN": "芬蘭",
    "ISL": "冰島", "POL": "波蘭", "CZE": "捷克", "SVK": "斯洛伐克", "HUN": "匈牙利", "SLO": "斯洛維尼亞",
    "CRO": "克羅埃西亞", "SRB": "塞爾維亞", "BUL": "保加利亞", "ROU": "羅馬尼亞", "UKR": "烏克蘭",
    "RUS": "俄羅斯", "BLR": "白俄羅斯", "EST": "愛沙尼亞", "LAT": "拉脫維亞", "LTU": "立陶宛",
    "GRE": "希臘", "CYP": "賽普勒斯", "AZE": "亞塞拜然", "ARM": "亞美尼亞", "GEO": "喬治亞",
    "MDA": "摩爾多瓦", "LUX": "盧森堡", "MLT": "馬爾他", "USA": "美國", "CAN": "加拿大", "MEX": "墨西哥",
    "GUA": "瓜地馬拉", "CRC": "哥斯大黎加", "PAN": "巴拿馬", "CUB": "古巴", "DOM": "多明尼加",
    "JAM": "牙買加", "PUR": "波多黎各", "ESA": "薩爾瓦多", "BRA": "巴西", "ARG": "阿根廷", "CHI": "智利",
    "PER": "秘魯", "COL": "哥倫比亞", "ECU": "厄瓜多", "VEN": "委內瑞拉", "PAR": "巴拉圭", "URU": "烏拉圭",
    "SUR": "蘇利南", "AUS": "澳洲", "NZL": "紐西蘭", "FIJ": "斐濟", "TAH": "大溪地",
    "NCL": "新喀里多尼亞", "RSA": "南非", "EGY": "埃及", "NGR": "奈及利亞", "ALG": "阿爾及利亞",
    "MAR": "摩洛哥", "TUN": "突尼西亞", "UGA": "烏干達", "KEN": "肯亞", "MRI": "模里西斯", "GHA": "迦納",
    "ZAM": "尚比亞", "ZIM": "辛巴威", "ETH": "衣索比亞", "BOT": "波札那", "CMR": "喀麥隆", "SEY": "塞席爾",
}

# 賽事通稱。由上而下比對，先放較具體的（Masters 在 Open 前、World Tour Finals 在 World 前）
TOURNAMENT = [
    (r"world tour finals|superseries finals", "年終總決賽"),
    (r"world championships", "世界錦標賽"),
    (r"olympic", "奧運"),
    (r"thomas\s*&\s*uber cup", "湯尤盃"),
    (r"sudirman cup", "蘇迪曼盃"),
    (r"asian games", "亞運"),
    (r"commonwealth games", "大英國協運動會"),
    (r"asia(n)? championships", "亞洲錦標賽"),
    (r"european championships", "歐洲錦標賽"),
    (r"all england", "全英賽"),
    (r"china masters", "中國大師賽"), (r"china open", "中國公開賽"),
    (r"indonesia masters", "印尼大師賽"), (r"indonesia open", "印尼公開賽"),
    (r"malaysia masters", "馬來西亞大師賽"), (r"malaysia open", "馬來西亞公開賽"),
    (r"japan masters", "日本大師賽"), (r"japan open", "日本公開賽"),
    (r"korea masters", "韓國大師賽"), (r"korea open", "韓國公開賽"),
    (r"thailand masters", "泰國大師賽"), (r"thailand open", "泰國公開賽"),
    (r"kaohsiung masters", "高雄大師賽"), (r"taipei open", "台北公開賽"),
    (r"hong kong open", "香港公開賽"), (r"macau open", "澳門公開賽"), (r"singapore open", "新加坡公開賽"),
    (r"india open", "印度公開賽"), (r"denmark open", "丹麥公開賽"), (r"french open", "法國公開賽"),
    (r"swiss open", "瑞士公開賽"), (r"german open", "德國公開賽"), (r"dutch open", "荷蘭公開賽"),
    (r"orleans masters", "奧爾良大師賽"), (r"spain masters", "西班牙大師賽"),
    (r"australian open", "澳洲公開賽"), (r"canada open", "加拿大公開賽"), (r"\bus open", "美國公開賽"),
    (r"arctic open", "北極公開賽"),
]
_TOURNAMENT = [(re.compile(p, re.I), zh) for p, zh in TOURNAMENT]
_YEAR = re.compile(r"\b(20\d{2})\b")
_QUAL = re.compile(r"^Qual\.?\s*(.+)$", re.I)
_ROUND_N = re.compile(r"^R(\d)$")


def event(code: str) -> str:
    return EVENT.get(code, code)


def round_name(r: str | None) -> str:
    if not r:
        return ""
    if r in ROUND:
        return ROUND[r]
    q = _QUAL.match(r)
    if q:
        return "資格賽 " + round_name(q.group(1))
    n = _ROUND_N.match(r)
    if n:                                   # 團體賽小組賽 R1–R3
        return f"第 {n.group(1)} 輪"
    return r


def status(s: str | None) -> str:
    return STATUS.get(s, s or "")


def country(code: str | None) -> str:
    if not code:
        return ""
    return "／".join(COUNTRY.get(c, c) for c in code.split("/"))


def competition(name: str | None) -> str:
    return COMPETITION.get(name, name or "")


def level(code: str | None) -> str:
    return LEVEL.get(code, code or "")


def tournament(name: str) -> str:
    """常見賽事轉中文通稱並保留年份；查不到就回傳原名。"""
    for pat, zh in _TOURNAMENT:
        if pat.search(name):
            y = _YEAR.search(name)
            return f"{y.group(1)} {zh}" if y else zh
    return name


def player(name_en: str, name_zh: str | None) -> str:
    return f"{name_zh}（{name_en}）" if name_zh else name_en


# ---------------------------------------------------------------- 選手中文名對照表
def official_table_exists() -> bool:
    return OFFICIAL_CSV.exists()


def load_player_table(path: Path | None = None) -> list[dict]:
    """[{player_id, name_zh, name_en, track}]。預設讀 config/players_zh.csv（Raymond 的正式名單，欄位：
    類型, bwf_player_id, 英文名（BWF）, 中文名, 暱稱, 目前排名, 追蹤（Y/N）, 備註）；不存在時讀 brief/player_zh.csv。
    只收有 BWF ID 與中文名的「選手」列；中文名一律照表，不自行翻譯。"""
    if path is None:
        path = OFFICIAL_CSV if OFFICIAL_CSV.exists() else PLAYER_ZH_CSV
    with path.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if rows and "bwf_player_id" in rows[0]:
        return [{"player_id": r["bwf_player_id"].strip(), "name_zh": r["中文名"].strip(),
                 "name_en": r["英文名（BWF）"].strip(), "track": (r.get("追蹤（Y/N）") or "Y").strip().upper() or "Y"}
                for r in rows if r.get("類型", "").strip() == "選手" and r["bwf_player_id"].strip() and r["中文名"].strip()]
    return [{**r, "track": r.get("track", "Y")} for r in rows if not r["player_id"].startswith("#")]


def apply_player_names(con, path: Path = PLAYER_ZH_CSV) -> int:
    """把對照表寫進 player.name_zh。只更新已存在的選手；回傳更新筆數。"""
    n = 0
    for r in load_player_table(path):
        n += con.execute("UPDATE player SET name_zh=? WHERE player_id=? AND COALESCE(name_zh, '') != ?",
                         (r["name_zh"], int(r["player_id"]), r["name_zh"])).rowcount
    con.commit()
    return n
