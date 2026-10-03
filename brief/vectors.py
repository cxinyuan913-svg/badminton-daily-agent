"""交接單 007：新聞向量庫與混合檢索（本機）。

把 `brief.db` 已存的新聞全文（`foreign_article`）切段 → BGE-M3 轉向量 → 存進另一個資料庫 `data/vectors.db`
（不放進 brief.db、不備份：隨時可以從 brief.db 的全文重算）。

檢索：FTS5（trigram，中文不用斷詞）BM25 前 30 ＋ 向量前 30 → RRF 合併 → 依日期與選手過濾 → 前 k 段（附 url、日期、來源）。
有選手或日期過濾時，先圈出符合的段落，再在這些段落裡算 BM25 與向量相似度（避免「全庫前 30 名都被過濾掉」）。

模型只在有新文章時載入，處理完釋放 GPU 記憶體；沒有 GPU 自動退回 CPU。測試用假的 embedding 函式，不載入真模型。

  python -m brief.vectors --db data/brief.db                       # 增量：只處理還沒轉過的文章
  python -m brief.vectors --db data/brief.db --rebuild             # 從頭重算
  python -m brief.vectors --db data/brief.db --query "戴資穎 傷勢" [--player 34810] [--since 2024-10-01]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sqlite3
import struct
import time
from pathlib import Path
from typing import Callable, Iterable

ROOT = Path(__file__).resolve().parent.parent
VECTORS_DB = ROOT / "data" / "vectors.db"
LOCK = ROOT / "data" / "vectors.lock"   # 建庫／增量同時只跑一個（watch 每 30 分鐘一次，第一次建庫可能更久）
LOCK_STALE_SEC = 3 * 3600
MODEL = "BAAI/bge-m3"
DIM = 1024
ZH_SIZE, ZH_OVERLAP = 450, 50        # 中文：每段 400–500 字、重疊 50 字（交接單）
EN_SIZE, EN_OVERLAP = 1200, 150      # 英文以字元計：1,200 字元約 200 詞、約 300 token，和中文 450 字的 token 數相近
TOP_N = 30
RRF_K = 60
BATCH = 8                            # GTX 1650（4 GB）：fp16、batch 8、最長 512 token
MIN_EN_KEY = 8                       # 英文名去掉空白後至少 8 個字母才比對（太短容易誤中）

Embed = Callable[[list[str]], list[list[float]]]

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id    INTEGER PRIMARY KEY,
    url         TEXT NOT NULL,
    seq         INTEGER NOT NULL,            -- 段號（0 起）
    source      TEXT,
    published   TEXT,
    title       TEXT,
    text        TEXT NOT NULL,
    UNIQUE (url, seq)
);
CREATE TABLE IF NOT EXISTS chunk_player (
    chunk_id    INTEGER NOT NULL,
    player_id   INTEGER NOT NULL,
    PRIMARY KEY (chunk_id, player_id)
);
CREATE INDEX IF NOT EXISTS ix_chunk_player ON chunk_player(player_id);
CREATE TABLE IF NOT EXISTS done_article (
    url         TEXT PRIMARY KEY,
    chunks      INTEGER NOT NULL,
    indexed_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(title, text, tokenize='trigram');
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(embedding float[{DIM}]);
"""


# ---------------------------------------------------------------- 資料庫
def connect(path: Path | str = VECTORS_DB) -> sqlite3.Connection:
    import sqlite_vec
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.enable_load_extension(True)
    sqlite_vec.load(con)
    con.enable_load_extension(False)
    con.executescript(SCHEMA)
    return con


def _blob(vec: Iterable[float]) -> bytes:
    v = list(vec)
    return struct.pack(f"{len(v)}f", *v)


def _unblob(b: bytes) -> list[float]:
    return list(struct.unpack(f"{len(b) // 4}f", b))


# ---------------------------------------------------------------- 切段
def is_english(text: str) -> bool:
    cjk = len(re.findall(r"[一-鿿]", text))
    return cjk < 0.05 * max(len(text), 1)


SENT_END = re.compile(r"[。！？!?.\n]")


def split(text: str, size: int | None = None, overlap: int | None = None) -> list[str]:
    """切成 size 字左右、重疊 overlap 字的段落；盡量在句尾切（段落後 40% 範圍內找最後一個句尾）。"""
    text = re.sub(r"[ \t ]+", " ", (text or "").strip())
    if not text:
        return []
    en = is_english(text)
    size = size or (EN_SIZE if en else ZH_SIZE)
    overlap = overlap if overlap is not None else (EN_OVERLAP if en else ZH_OVERLAP)
    out, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            window = text[start + int(size * 0.6): end]
            ends = [m.end() for m in SENT_END.finditer(window)]
            if ends:
                end = start + int(size * 0.6) + ends[-1]
        piece = text[start:end].strip()
        if piece:
            out.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return out


# ---------------------------------------------------------------- 選手比對
def _letters(s: str) -> str:
    return re.sub(r"[^a-z]", "", s.lower())


class NameIndex:
    """段落提到哪些選手：中文名（台灣選手名單、Raymond 確認或雙來源的外國譯名）、暱稱、英文全名（去空白比對，
    只收曾進官方排名的選手，避免同名的青少年或地方選手）。"""

    def __init__(self, zh: dict[str, set[int]], en: dict[str, set[int]]):
        self.zh, self.en = zh, en

    @classmethod
    def build(cls, con) -> "NameIndex":
        zh: dict[str, set[int]] = {}
        en: dict[str, set[int]] = {}
        for pid, name in con.execute("SELECT player_id, name_zh FROM player WHERE name_zh IS NOT NULL AND name_zh <> ''"):
            zh.setdefault(name, set()).add(pid)
        for sql in ("SELECT player_id, name_zh FROM foreign_name WHERE status='confirmed'",):
            try:
                for pid, name in con.execute(sql):
                    if name:
                        zh.setdefault(name, set()).add(pid)
            except sqlite3.OperationalError:
                pass
        try:
            for nick, ids in con.execute("SELECT nickname, player_ids FROM nickname WHERE status IN ('auto', 'confirmed') AND player_ids <> ''"):
                zh.setdefault(nick, set()).update(int(x) for x in ids.split(",") if x.strip().isdigit())
        except sqlite3.OperationalError:
            pass
        ranked = """SELECT DISTINCT p.player_id, p.name_display FROM player p
                    JOIN pairing pr ON p.player_id IN (pr.player_a_id, pr.player_b_id)
                    JOIN ranking_snapshot r ON r.pairing_id = pr.pairing_id"""
        for pid, name in con.execute(ranked):
            key = _letters(name or "")
            if len(key) >= MIN_EN_KEY:
                en.setdefault(key, set()).add(pid)
        return cls({k: v for k, v in zh.items() if len(k) >= 2}, en)

    def players(self, text: str) -> set[int]:
        found: set[int] = set()
        for name, ids in self.zh.items():
            if name in text:
                found |= ids
        flat = _letters(text)
        for key, ids in self.en.items():
            if key in flat:
                found |= ids
        return found


# ---------------------------------------------------------------- 模型
class BGEM3:
    """BGE-M3 dense 向量（sentence-transformers）。第一次呼叫才載入；release() 釋放 GPU 記憶體。"""

    def __init__(self, model: str = MODEL, batch: int = BATCH, device: str | None = None):
        self.name, self.batch, self.model, self.device = model, batch, None, device
        self.peak_vram_mb = 0.0

    def _load(self):
        import torch
        from sentence_transformers import SentenceTransformer
        self.device = self.device or ("cuda" if torch.cuda.is_available() else "cpu")
        m = SentenceTransformer(self.name, device=self.device)
        if self.device == "cuda":
            m = m.half()
            torch.cuda.reset_peak_memory_stats()
        m.max_seq_length = 512
        self.model = m

    def __call__(self, texts: list[str]) -> list[list[float]]:
        if self.model is None:
            self._load()
        vecs = self.model.encode(texts, batch_size=self.batch, normalize_embeddings=True, convert_to_numpy=True)
        if self.device == "cuda":
            import torch
            self.peak_vram_mb = max(self.peak_vram_mb, torch.cuda.max_memory_allocated() / 2**20)
        return vecs.astype("float32").tolist()

    def release(self) -> None:
        if self.model is None:
            return
        self.model = None
        try:
            import gc
            import torch
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:  # noqa: BLE001
            pass


def available() -> bool:
    """向量套件（torch、sentence-transformers、sqlite-vec）有裝才做；沒裝就安靜跳過（排程不因此失敗）。
    只查套件在不在、不 import（import torch 要好幾秒，watch 每 30 分鐘跑一次）。"""
    from importlib.util import find_spec
    return all(find_spec(m) is not None for m in ("torch", "sentence_transformers", "sqlite_vec"))


class Locked(Exception):
    pass


class lock:
    """檔案鎖（O_EXCL 建檔）：另一個建庫或增量在跑就丟 Locked；超過 3 小時的鎖當作殘留，直接清掉。"""

    def __init__(self, path: Path = LOCK):
        self.path = path

    def __enter__(self):
        import os
        if self.path.exists() and time.time() - self.path.stat().st_mtime > LOCK_STALE_SEC:
            self.path.unlink(missing_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise Locked(str(self.path)) from None
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return self

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)


def update(con, log: Callable[[str], None] = lambda s: None) -> dict | None:
    """新聞收集後的增量（watch 每次跑）：向量庫已建（跑過一次 CLI）才做；沒有新文章就不載入模型；
    另一個建庫或增量在跑就跳過（下次再補）。"""
    if not available() or not VECTORS_DB.exists():
        return None
    try:
        with lock():
            vcon = connect()
            try:
                return index_new(con, vcon, log=log)
            finally:
                vcon.close()
    except Locked:
        return None


# ---------------------------------------------------------------- 建索引
def pending(con, vcon, rebuild: bool = False) -> list[tuple]:
    done = set() if rebuild else {r[0] for r in vcon.execute("SELECT url FROM done_article")}
    rows = con.execute("""SELECT a.url, n.source, n.published, n.title, a.text FROM foreign_article a
                          LEFT JOIN news_item n USING (url) ORDER BY n.published""").fetchall()
    return [r for r in rows if r[0] not in done and (r[4] or "").strip()]


def clear(vcon) -> None:
    for t in ("chunks", "chunk_player", "done_article", "chunks_fts", "chunks_vec"):
        vcon.execute(f"DELETE FROM {t}")
    vcon.commit()


def index_new(con, vcon, embed: Embed | None = None, rebuild: bool = False, names: NameIndex | None = None,
              log: Callable[[str], None] = lambda s: None) -> dict:
    """把還沒轉過的文章切段、標選手、轉向量。冪等：同一篇（url）只處理一次；rebuild 先清空。
    embed 沒給就用 BGE-M3（只有在有新文章時才載入）。回傳統計。"""
    if rebuild:
        clear(vcon)
    todo = pending(con, vcon)
    stats = {"articles": len(todo), "chunks": 0, "seconds": 0.0, "device": None, "peak_vram_mb": None}
    if not todo:
        return stats
    names = names or NameIndex.build(con)
    own = embed is None
    embed = embed or BGEM3()
    t0 = time.time()
    try:
        buf: list[tuple[int, str]] = []

        def flush():
            if not buf:
                return
            vecs = embed([f"{t}" for _, t in buf])
            vcon.executemany("INSERT INTO chunks_vec (rowid, embedding) VALUES (?, ?)",
                             [(cid, _blob(v)) for (cid, _), v in zip(buf, vecs)])
            buf.clear()

        for i, (url, source, published, title, text) in enumerate(todo, 1):
            pieces = split(text)
            for seq, piece in enumerate(pieces):
                cur = vcon.execute("INSERT INTO chunks (url, seq, source, published, title, text) VALUES (?,?,?,?,?,?)",
                                   (url, seq, source, published, title, piece))
                cid = cur.lastrowid
                vcon.execute("INSERT INTO chunks_fts (rowid, title, text) VALUES (?, ?, ?)", (cid, title or "", piece))
                vcon.executemany("INSERT OR IGNORE INTO chunk_player VALUES (?, ?)",
                                 [(cid, p) for p in names.players((title or "") + "\n" + piece)])
                buf.append((cid, piece))
                if len(buf) >= 64:
                    flush()
            flush()
            vcon.execute("INSERT OR REPLACE INTO done_article (url, chunks) VALUES (?, ?)", (url, len(pieces)))
            vcon.commit()                       # 每篇提交：中斷後重跑只補沒做完的
            stats["chunks"] += len(pieces)
            if i % 100 == 0:
                log(f"{i}/{len(todo)} 篇，{stats['chunks']} 段，{time.time() - t0:.0f} 秒")
    finally:
        stats["seconds"] = round(time.time() - t0, 1)
        if isinstance(embed, BGEM3):
            stats["device"], stats["peak_vram_mb"] = embed.device, round(embed.peak_vram_mb)
            if own:
                embed.release()
    return stats


# ---------------------------------------------------------------- 檢索
def rrf(rankings: list[list[int]], k: int = RRF_K) -> dict[int, float]:
    """Reciprocal Rank Fusion：每個清單第 r 名（1 起）得 1/(k+r)，加總。"""
    score: dict[int, float] = {}
    for ranking in rankings:
        for r, cid in enumerate(ranking, 1):
            score[cid] = score.get(cid, 0.0) + 1.0 / (k + r)
    return score


def fts_query(query: str) -> str | None:
    """trigram 只能比對 ≥ 3 個字元的詞：每個詞加引號、用 OR 串起來；太短的詞交給向量。"""
    terms = [t for t in re.split(r"[\s，、,。]+", query) if len(t) >= 3]
    return " OR ".join('"' + t.replace('"', '""') + '"' for t in terms) or None


def candidates(vcon, player_ids: Iterable[int] | None = None, since: str | None = None,
               until: str | None = None) -> list[int] | None:
    """符合選手／日期過濾的段落；沒有任何過濾回傳 None（代表全庫）。"""
    player_ids = list(player_ids or [])
    if not player_ids and not since and not until:
        return None
    sql, args = "SELECT c.chunk_id FROM chunks c WHERE 1=1", []
    if player_ids:
        sql += f" AND c.chunk_id IN (SELECT chunk_id FROM chunk_player WHERE player_id IN ({','.join('?' * len(player_ids))}))"
        args += player_ids
    if since:
        sql += " AND substr(c.published, 1, 10) >= ?"
        args.append(since[:10])
    if until:
        sql += " AND substr(c.published, 1, 10) <= ?"
        args.append(until[:10])
    return [r[0] for r in vcon.execute(sql, args)]


def _fts_rank(vcon, query: str, cand: list[int] | None) -> list[int]:
    q = fts_query(query)
    if not q:
        return []
    if cand is None:
        rows = vcon.execute("SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts) LIMIT ?",
                            (q, TOP_N)).fetchall()
    else:
        if not cand:
            return []
        vcon.execute("CREATE TEMP TABLE IF NOT EXISTS _cand (id INTEGER PRIMARY KEY)")
        vcon.execute("DELETE FROM _cand")
        vcon.executemany("INSERT INTO _cand VALUES (?)", [(c,) for c in cand])
        rows = vcon.execute("""SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? AND rowid IN (SELECT id FROM _cand)
                               ORDER BY bm25(chunks_fts) LIMIT ?""", (q, TOP_N)).fetchall()
    return [r[0] for r in rows]


def _vec_rank(vcon, qvec: list[float], cand: list[int] | None) -> list[int]:
    if cand is None:
        rows = vcon.execute("SELECT rowid FROM chunks_vec WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                            (_blob(qvec), TOP_N)).fetchall()
        return [r[0] for r in rows]
    scored = []
    for i in range(0, len(cand), 500):
        part = cand[i:i + 500]
        for cid, blob in vcon.execute(f"SELECT rowid, embedding FROM chunks_vec WHERE rowid IN ({','.join('?' * len(part))})", part):
            v = _unblob(blob)
            scored.append((sum(a * b for a, b in zip(qvec, v)), cid))     # 向量已正規化：內積＝cosine
    return [cid for _, cid in sorted(scored, reverse=True)[:TOP_N]]


def search(vcon, query: str, embed: Embed, k: int = 3, player_ids: Iterable[int] | None = None,
           since: str | None = None, until: str | None = None, per_article: int = 1) -> list[dict]:
    """混合檢索：BM25 前 30 ＋向量前 30 → RRF → 前 k 段。同一篇文章最多 per_article 段（避免三段都來自同一篇）。"""
    cand = candidates(vcon, player_ids, since, until)
    fts = _fts_rank(vcon, query, cand)
    vec = _vec_rank(vcon, embed([query])[0], cand)
    score = rrf([fts, vec])
    out, per = [], {}
    for cid, s in sorted(score.items(), key=lambda kv: -kv[1]):
        row = vcon.execute("SELECT chunk_id, url, seq, source, published, title, text FROM chunks WHERE chunk_id=?", (cid,)).fetchone()
        if row is None or per.get(row[1], 0) >= per_article:
            continue
        per[row[1]] = per.get(row[1], 0) + 1
        out.append(dict(zip(["chunk_id", "url", "seq", "source", "published", "title", "text"], row),
                        score=round(s, 5), via=[n for n, r in (("bm25", fts), ("vector", vec)) if cid in r]))
        if len(out) >= k:
            break
    return out


def stats(vcon) -> dict:
    return {"articles": vcon.execute("SELECT count(*) FROM done_article").fetchone()[0],
            "chunks": vcon.execute("SELECT count(*) FROM chunks").fetchone()[0],
            "tagged_chunks": vcon.execute("SELECT count(DISTINCT chunk_id) FROM chunk_player").fetchone()[0],
            "size_mb": round(Path(VECTORS_DB).stat().st_size / 2**20, 1) if Path(VECTORS_DB).exists() else None}


# ---------------------------------------------------------------- 給故事引擎與粉專
KIND_QUERY = {"rivalry": "宿敵 交手 對決 rivalry", "domination": "連勝 壓制 dominance winning streak",
              "revenge": "復仇 雪恥 revenge", "stuck_round": "止步 卡關 突破 breakthrough",
              "record": "紀錄 首次 record history", "retired": "退賽 傷勢 受傷 injury retired",
              "reunion": "拆夥 重組 搭檔 換搭檔 new partner reunion"}


def player_ids_for(con, names: Iterable[str]) -> list[int]:
    """故事裡的選手名（中文或英文，組合用 / 分隔）→ BWF 選手 ID。"""
    ids: set[int] = set()
    for name in names:
        for part in re.split(r"\s*[/／]\s*", name or ""):
            part = part.strip()
            if not part:
                continue
            for (pid,) in con.execute("SELECT player_id FROM player WHERE name_display=? OR name_zh=?", (part, part)):
                ids.add(pid)
            try:
                for (pid,) in con.execute("SELECT player_id FROM foreign_name WHERE name_zh=? AND status='confirmed'", (part,)):
                    ids.add(pid)
            except sqlite3.OperationalError:
                pass
    return sorted(ids)


def news_for_story(con, names: Iterable[str], upto: str, kind: str | None = None, days: int = 30, k: int = 3,
                   vcon=None, embed: Embed | None = None) -> list[str] | None:
    """【新聞】區（R11）：主角最近 days 天的新聞，用向量庫依「選手名＋故事類型」語意排序，取前 k 段，附出處。
    向量庫沒建、套件沒裝、或找不到主角的選手 ID → 回傳 None，呼叫端退回原本的關鍵字比對。"""
    names = [n for n in names if n]
    if vcon is None and (not available() or not VECTORS_DB.exists()):
        return None
    ids = player_ids_for(con, names)
    if not ids:
        return None
    since = (dt.date.fromisoformat(upto[:10]) - dt.timedelta(days=days)).isoformat()
    own_con, own_embed = vcon is None, embed is None
    vcon = vcon or connect()
    embed = embed or BGEM3(device="cpu" if LOCK.exists() else None)   # 建庫中（佔著 GPU）→ 查詢用 CPU，避免 4 GB 顯示卡放不下兩份模型
    try:
        query = " ".join(names) + " " + KIND_QUERY.get(kind or "", "")
        hits = search(vcon, query.strip(), embed, k=k, player_ids=ids, since=since, until=upto)
    finally:
        if own_embed and isinstance(embed, BGEM3):
            embed.release()
        if own_con:
            vcon.close()
    return [f"新聞：{h['title']}（{h['source']}，{(h['published'] or '')[:10]}，{h['url']}）：{h['text'][:200]}" for h in hits]


# ---------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="data/brief.db")
    ap.add_argument("--vectors", default=str(VECTORS_DB))
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--query")
    ap.add_argument("--player", type=int, action="append")
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("-k", type=int, default=3)
    a = ap.parse_args()
    con = sqlite3.connect(a.db)
    vcon = connect(a.vectors)
    if a.query:
        embed = BGEM3()
        for h in search(vcon, a.query, embed, k=a.k, player_ids=a.player, since=a.since, until=a.until):
            print(f"[{h['score']}｜{'+'.join(h['via'])}] {(h['published'] or '')[:10]} {h['source']}｜{h['title']}\n    {h['url']}\n    {h['text'][:160]}…")
        embed.release()
        return
    with lock():
        res = index_new(con, vcon, rebuild=a.rebuild, log=print)
    print(json.dumps({**res, **stats(vcon)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
