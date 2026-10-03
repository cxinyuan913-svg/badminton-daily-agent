"""台灣歷史選手中文名：中華羽協甲組名單（男、女，歷年累積）拼音比對 BWF 英文名（notes 10-02 05:55 第 5 項）。

名單 PDF：https://www.ctb.org.tw/information.asp?id=123 下載，存 config/ctba/（PDF 不進版控；robots.txt 404＝不限制）。
比對：BWF 名字拆成「姓（全大寫）＋名」，和名單每個中文名的漢語拼音、威妥瑪拼音（去聲調、去符號）比編輯距離；
姓要對得上。距離 0–1 且只有一個最佳候選 → 自動採用（寫進 config/players_zh.csv，追蹤 N、備註「歷史選手，自動比對」）；
2–3 或有多個同分候選 → 列進 config/ctba/review.csv 請 Raymond 確認。

用法：python -m brief.ctba --db data/brief.db [--write]
"""
from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CTBA_DIR = ROOT / "config" / "ctba"
REVIEW_CSV = CTBA_DIR / "review.csv"
PLAYERS_CSV = ROOT / "config" / "players_zh.csv"
# notes 05:55 寫 0–1 自動採用；實跑發現距離 1 有錯（「YANG Po Chieh → 楊博智」，智是 chih），所以只有 0 自動採用，
# 1 列「建議採用」請 Raymond 確認（事實正確優先；status.md 待決定）
AUTO_MAX, SUGGEST_MAX, REVIEW_MAX = 0, 1, 3
NOTE = "歷史選手，自動比對（中華羽協甲組名單 115/08/19）"
CJK = re.compile(r"^[一-鿿‧.]+$")


def roster_names(text: str) -> list[str]:
    """PDF 文字 → 中文名清單。處理：名字中間的空白（「陳    康」「陳　功」）、跨行斷掉的名字（「范姜明／盛」）、
    「(殁)」「(大)」等註記、「○○年…晉升名單」段落標題。"""
    text = re.sub(r"\((?:殁|大|小)\)", "", text)
    text = re.sub(r"\(改名成\s*([一-鿿]+)\)", r" \1 ", text)          # 「洪桑笛 (改名成 李忻瑋)」兩個都留
    names: list[str] = []
    for line in text.splitlines():
        if "晉升名單" in line or not line.strip():
            continue
        line = line.replace("\xa0", " ")                    # PDF 用不斷行空白對齊兩字名（「陳 \xa0\xa0 康」）
        # 兩字名中間的對齊空白：半形 2 個以上或全形 1 個（名字之間只隔 1 個半形空白）
        line = re.sub(r"(?<![一-鿿])([一-鿿])(?: {2,}|　+)([一-鿿])(?![一-鿿])", r"\1\2", line)
        toks = [t for t in re.split(r"[\s　]+", line.strip()) if t]
        for i, t in enumerate(toks):
            if not CJK.match(t):
                continue
            if i == 0 and len(t) == 1 and names:          # 上一行最後一個名字被斷行（「范姜明」＋「盛」）
                names[-1] += t
                continue
            names.append(t)
    return [n for n in dict.fromkeys(names) if 2 <= len(n) <= 6]


def load_rosters() -> dict[str, list[str]]:
    import pypdf
    out = {}
    for gender, pattern in (("M", "男子甲組*.pdf"), ("F", "女子甲組*.pdf")):
        names = []
        for p in sorted(CTBA_DIR.glob(pattern)):
            text = "\n".join(page.extract_text() or "" for page in pypdf.PdfReader(str(p)).pages)
            names += roster_names(text)
        out[gender] = list(dict.fromkeys(names))
    return out


# ---------------------------------------------------------------- 拼音
def _norm(s: str) -> str:
    return re.sub(r"[^a-z]", "", s.lower().replace("ü", "u").replace("ê", "e"))


def renderings(name_zh: str) -> list[tuple[str, str]]:
    """[(姓的拼法, 全名拼法)]：漢語拼音、威妥瑪；複姓（范姜、張簡）兩字都算姓。"""
    from pypinyin import Style, lazy_pinyin
    out = []
    for style in (Style.NORMAL, Style.WADEGILES):
        syl = [_norm(x) for x in lazy_pinyin(name_zh, style=style)]
        for k in (1, 2) if len(name_zh) >= 3 else (1,):
            out.append(("".join(syl[:k]), "".join(syl)))
    return list(dict.fromkeys(out))


def split_bwf(name: str) -> tuple[str, str]:
    """「LIANG Ting Yu」「Yu Hsiang CHOU」→（姓, 名）。全大寫的字是姓。"""
    toks = re.split(r"[\s]+", name.strip())
    sur = [t for t in toks if t.isupper() and len(t) > 1]
    given = [t for t in toks if t not in sur]
    if not sur:                                            # 沒有全大寫：當作「姓 名」
        sur, given = toks[:1], toks[1:]
    return _norm("".join(sur)), _norm("".join(given))


def lev(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def score(bwf_name: str, name_zh: str) -> int | None:
    """編輯距離（越小越像）；姓對不上（距離 > 1）回傳 None。"""
    sur, given = split_bwf(bwf_name)
    best = None
    for zs, full in renderings(name_zh):
        if lev(sur, zs) > 1:
            continue
        d = lev(sur + given, full)
        best = d if best is None else min(best, d)
    return best


def match(bwf_name: str, roster: list[str]) -> list[tuple[int, str]]:
    """[(距離, 中文名)]，距離 ≤ REVIEW_MAX，由近到遠。"""
    hits = [(d, n) for n in roster if (d := score(bwf_name, n)) is not None and d <= REVIEW_MAX]
    return sorted(hits)


def gender_of(con, player_id: int) -> set[str]:
    """從打過的項目推性別：MS／MD → 男、WS／WD → 女；只打混雙 → 兩份名單都比。"""
    rows = con.execute("""SELECT DISTINCT m.event FROM match m JOIN pairing p ON p.pairing_id IN (m.side1_id, m.side2_id)
                          WHERE ? IN (p.player_a_id, p.player_b_id)""", (player_id,)).fetchall()
    ev = {r[0] for r in rows}
    g = ({"M"} if ev & {"MS", "MD"} else set()) | ({"F"} if ev & {"WS", "WD"} else set())
    return g or {"M", "F"}


def run(con, write: bool = False) -> dict:
    rosters = load_rosters()
    known = {int(x) for r in csv.DictReader(PLAYERS_CSV.open(encoding="utf-8-sig"))
             for x in (r.get("bwf_player_id") or "").split("+") if x.strip().isdigit()}     # 雙打列是「92913+96514」
    players = con.execute("SELECT player_id, name_display FROM player WHERE country_code='TPE' AND name_zh IS NULL "
                          "ORDER BY player_id").fetchall()
    auto, review = [], []
    for pid, name in players:
        if pid in known or not name:
            continue
        pool = [n for g in gender_of(con, pid) for n in rosters[g]]
        hits = match(name, pool)
        if not hits:
            continue
        top = [h for h in hits if h[0] == hits[0][0]]
        if hits[0][0] <= AUTO_MAX and len({n for _, n in top}) == 1:
            auto.append((pid, name, top[0][1], hits[0][0]))
        else:
            hint = "建議採用" if hits[0][0] <= SUGGEST_MAX and len({n for _, n in top}) == 1 else ""
            review.append((pid, name, "、".join(f"{n}({d})" for d, n in hits[:4]), hint))
    # 同一個中文名對到兩位以上選手 → 都改列待確認（例：Cheng CHEN、Jheng CHEN 都像「陳誠」）
    taken: dict[str, int] = {}
    for _, _, zh_name, _ in auto:
        taken[zh_name] = taken.get(zh_name, 0) + 1
    review += [(pid, name, f"{zh_name}(0)", "同名衝突") for pid, name, zh_name, _ in auto if taken[zh_name] > 1]
    auto = [row for row in auto if taken[row[2]] == 1]
    if write:
        with PLAYERS_CSV.open("a", encoding="utf-8", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            for pid, name, zh_name, d in auto:
                w.writerow(["選手", pid, name, zh_name, "", "", "N", f"{NOTE}，拼音距離 {d}"])
        with REVIEW_CSV.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["bwf_player_id", "英文名（BWF）", "候選中文名（拼音距離）", "提示", "Raymond 確認"])
            w.writerows([[pid, name, cands, hint, ""] for pid, name, cands, hint in review])
    return {"roster": {g: len(v) for g, v in rosters.items()}, "players": len(players), "auto": auto, "review": review}


# ---------------------------------------------------------------- 國內賽事名單（10-03 Raymond A）
# 甲組名單以外的選手（乙組、大專、青少年）：中華羽協「國內賽事」頁的排名賽、團體賽、青少年錦標賽 xlsx（種子序、賽程表、
# 成績、團體名單）裡的中文姓名。拼音距離 0 而且整個名單池只有一個中文名對得上 → 自動採用；其餘維持英文（D）。
EXTRA_DIR = CTBA_DIR / "extra"
EXTRA_PAGES = {221: "排名賽", 222: "團體賽", 223: "青少選拔"}
EXTRA_NOTE = "中華羽協國內賽事名單比對（排名賽／團體賽／青少年 xlsx），拼音距離 0、唯一"
NAME_TOKEN = re.compile(r"[一-鿿]{2,4}")


def fetch_extra(client, pages: dict[int, str] = EXTRA_PAGES, log=print) -> int:
    """下載國內賽事頁的 xlsx 到 config/ctba/extra/（已下載的跳過；Client 每次請求間隔 2 秒）。回傳新下載的檔數。"""
    import urllib.parse
    EXTRA_DIR.mkdir(parents=True, exist_ok=True)
    n = 0
    for pid, label in pages.items():
        r = client.get(f"https://www.ctb.org.tw/information.asp?id={pid}")
        if r is None:
            continue
        page = r.content.decode(r.apparent_encoding or "utf-8", errors="replace")
        for href in dict.fromkeys(re.findall(r'href="([^"]+\.xlsx)"', page, re.I)):
            name = urllib.parse.unquote(href.split("/")[-1])
            dest = EXTRA_DIR / f"{pid}_{name}"
            if dest.exists():
                continue
            f = client.get(urllib.parse.urljoin("https://www.ctb.org.tw/", href))
            if f is None:
                continue
            dest.write_bytes(f.content)
            n += 1
        log(f"{label}：{len(list(EXTRA_DIR.glob(f'{pid}_*.xlsx')))} 個 xlsx")
    return n


def xlsx_names(path: Path) -> set[str]:
    """xlsx 每個儲存格裡 2–4 個漢字的連續字串（選手名；隊名、組別也會混進來，但對不上 BWF 英文名，不影響）。"""
    import openpyxl
    out: set[str] = set()
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception:  # noqa: BLE001 — 壞檔或舊格式就跳過
        return out
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            for v in row:
                if isinstance(v, str):
                    out.update(NAME_TOKEN.findall(v))
    wb.close()
    return out


def extra_pool() -> list[str]:
    names: set[str] = set()
    for p in sorted(EXTRA_DIR.glob("*.xlsx")):
        names |= xlsx_names(p)
    return sorted(names)


def run_extra(con, write: bool = False, pool: list[str] | None = None) -> dict:
    """還沒有中文名、也不在 players_zh.csv 的台灣選手，用「甲組名單＋國內賽事名單」比對；只採用距離 0 且唯一的。"""
    rosters = load_rosters()
    pool = list(dict.fromkeys((pool if pool is not None else extra_pool()) + rosters["M"] + rosters["F"]))
    known = {int(x) for r in csv.DictReader(PLAYERS_CSV.open(encoding="utf-8-sig"))
             for x in (r.get("bwf_player_id") or "").split("+") if x.strip().isdigit()}
    players = con.execute("SELECT player_id, name_display FROM player WHERE country_code='TPE' AND name_zh IS NULL "
                          "ORDER BY player_id").fetchall()
    auto, ambiguous, none = [], [], []
    for pid, name in players:
        if pid in known or not name:
            continue
        exact = sorted({n for n in pool if score(name, n) == 0})
        if len(exact) == 1:
            auto.append((pid, name, exact[0]))
        elif exact:
            ambiguous.append((pid, name, "、".join(exact)))
        else:
            none.append((pid, name))
    taken: dict[str, int] = {}                     # 同一個中文名對到兩位以上選手 → 都不自動採用
    for _, _, z in auto:
        taken[z] = taken.get(z, 0) + 1
    ambiguous += [(pid, name, f"{z}（同名對到多位選手）") for pid, name, z in auto if taken[z] > 1]
    auto = [row for row in auto if taken[row[2]] == 1]
    if write and auto:
        with PLAYERS_CSV.open("a", encoding="utf-8", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            for pid, name, z in auto:
                w.writerow(["選手", pid, name, z, "", "", "N", EXTRA_NOTE])
    return {"pool": len(pool), "players": len(auto) + len(ambiguous) + len(none),
            "auto": auto, "ambiguous": ambiguous, "none": none}


def main():
    from brief.crawler import connect
    ap = argparse.ArgumentParser(description="中華羽協甲組名單 → 台灣歷史選手中文名")
    ap.add_argument("--db", default="data/brief.db")
    ap.add_argument("--write", action="store_true", help="寫進 config/players_zh.csv 與 config/ctba/review.csv")
    ap.add_argument("--extra", action="store_true", help="改用甲組＋國內賽事名單（排名賽、團體賽、青少年 xlsx），只採用距離 0 且唯一")
    ap.add_argument("--fetch", action="store_true", help="先下載國內賽事 xlsx（搭配 --extra）")
    a = ap.parse_args()
    if a.extra:
        from brief import zh
        from brief.crawler import Client
        con = connect(a.db)
        if a.fetch:
            print("新下載", fetch_extra(Client()), "個檔案")
        res = run_extra(con, a.write)
        print(f"名單池 {res['pool']} 個中文名；還沒有中文名的台灣選手 {res['players']} 位")
        print(f"自動採用 {len(res['auto'])}、多個完全一致 {len(res['ambiguous'])}、找不到 {len(res['none'])}")
        for row in res["auto"]:
            print("  採用", row)
        for row in res["ambiguous"]:
            print("  多個", row)
        if a.write:
            print("套用到資料庫", zh.apply_player_names(con))
        return
    res = run(connect(a.db), a.write)
    print(f"名單：男 {res['roster']['M']}、女 {res['roster']['F']} 人；沒有中文名的台灣選手 {res['players']} 位")
    print(f"自動採用 {len(res['auto'])}、待確認 {len(res['review'])}")
    for row in res["auto"][:15]:
        print("  採用", row)


if __name__ == "__main__":
    main()


HEADER = re.compile(r"(\d{3})年第一、二次排名賽晉升名單")


def roster_years(text_pages: list[str]) -> dict[str, str]:
    """{中文名: 晉升年份標籤}（notes 07:25 第 5 點）。PDF 的 105–108 年段落標題在名字前面（可靠）；
    109–115 年的標題擠在頁尾、順序也亂了，所以 108 年標題之後的名字只能標「108–115 年」；第一個標題前是原始名單。"""
    out: dict[str, str] = {}
    label = "原始名單（105 年以前）"
    for page in text_pages:
        pos = 0
        for m in HEADER.finditer(page):
            chunk = page[pos:m.start()]
            for n in roster_names(chunk):
                out.setdefault(n, label)
            year = int(m.group(1))
            # 108 年標題之後到頁尾那團標題之間，其實還有 109–115 年的名字（那幾年的標題被擠到後面）→ 108 起無法細分
            label = f"{year} 年晉升" if year < 108 else "108–115 年晉升（PDF 無法細分哪一年）"
            pos = m.end()
        for n in roster_names(page[pos:]):
            out.setdefault(n, label)
    return out


def write_distance1(review: list[tuple], path: Path | None = None) -> int:
    """config/ctba/review_distance1.csv：拼音距離 1 的「建議採用」，給 Raymond 分批確認。"""
    import pypdf
    path = path or CTBA_DIR / "review_distance1.csv"
    years: dict[str, str] = {}
    for p in sorted(CTBA_DIR.glob("*甲組*.pdf")):
        years.update(roster_years([pg.extract_text() or "" for pg in pypdf.PdfReader(str(p)).pages]))
    rows = []
    for pid, name, cands, hint in review:
        if hint != "建議採用":
            continue
        zh_name = cands.split("(")[0]
        rows.append([pid, name, zh_name, years.get(zh_name, "（名單裡找不到年份）"), ""])
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["bwf_player_id", "英文名（BWF）", "候選中文名", "晉升年份", "Raymond 確認（Y／N／正確中文名）"])
        w.writerows(rows)
    return len(rows)
