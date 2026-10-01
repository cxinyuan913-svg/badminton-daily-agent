"""晨報新聞摘要（notes 10-02 06:20 第 1 點）：每則新聞給標題＋來源＋日期＋2–3 句中文摘要＋連結。

- 內文：收集階段（brief.daily）把最近兩天、還沒抓過內文的新聞各抓一次，存 foreign_article（台灣媒體與 BWF 都存；
  只存不外流）。BWF 英文新聞一樣寫中文摘要。
- 摘要：晨報產生時一次批次呼叫 Opus low（task＝news_summary），同一事件多家報導合併成一則、列多個來源。
  摘要用自己的話改寫、不大段引用；數字與名字要能在那幾篇的標題＋內文找到（事實檢查不過 → 只列標題）。
- 內文沒抓到 → 寫「（只有標題）」，不要用標題硬編內容。
"""
from __future__ import annotations

import html
import json
import re

TAG = re.compile(r"<[^>]+>")
MAX_TEXT = 1800           # 每篇給模型的內文上限（字）

SYSTEM = """你是台灣羽球日報的編輯。使用者給你幾則新聞（標題、來源、內文節錄），請：
1. 把報導同一件事的新聞合併成一組（例如兩家媒體都報導周天成亞運八強）
2. 每組用繁體中文、台灣用語寫 2–3 句摘要：用自己的話改寫，不要整句照抄；英文新聞也寫中文摘要
3. 只能用內文裡的事實，名字、比分、數字照內文；不要補內文沒有的背景
4. 名字：台灣選手用中文；外國選手內文是中文就用中文，是英文就保留英文
只輸出 JSON：{"groups": [{"ids": [1, 3], "summary": "…"}]}；每個 id 都要出現在某一組"""


def page_text(page: str) -> str:
    paras = re.findall(r"<p[^>]*>(.*?)</p>", page, re.S)
    return "\n".join(t for t in (html.unescape(TAG.sub("", p)).strip() for p in paras) if t)


def fetch_texts(con, client, since: str, limit: int = 15) -> int:
    """最近的新聞還沒有內文的，各抓一次存 foreign_article（BWF 也存）。回傳抓了幾篇。"""
    from brief import foreign_names
    con.executescript(foreign_names.TABLE)
    rows = con.execute("""SELECT n.url, n.source FROM news_item n LEFT JOIN foreign_article a USING (url)
                          WHERE a.url IS NULL AND substr(n.published, 1, 10) >= ? ORDER BY n.published DESC LIMIT ?""",
                       (since, limit)).fetchall()
    n = 0
    for url, source in rows:
        try:                                                  # 一篇抓不到不影響其他篇
            if source in foreign_names.SOURCES.values():
                foreign_names.fetch(con, client, [url])      # 台灣媒體：順便擷取譯名
            else:
                r = client.get(url)
                if r is None:
                    continue
                con.execute("INSERT OR REPLACE INTO foreign_article (url, source, text) VALUES (?, ?, ?)",
                            (url, source, page_text(r.text)))
            n += 1
        except Exception:  # noqa: BLE001
            continue
    con.commit()
    return n


def texts(con, news: list[dict]) -> dict[str, str]:
    try:
        return {u: t for u, t in con.execute(
            f"SELECT url, text FROM foreign_article WHERE url IN ({','.join('?' * len(news))})", [n["url"] for n in news])}
    except Exception:  # noqa: BLE001
        return {}


def summarize(con, llm, news: list[dict], errors: list | None = None) -> list[dict]:
    """回傳 [{items: [新聞…], summary: str | None}]；summary None ＝只有標題（沒內文或沒通過檢查）。"""
    from brief.llm import unverified
    if not news:
        return []
    body = texts(con, news)
    with_text = [(i + 1, n) for i, n in enumerate(news) if (body.get(n["url"]) or "").strip()]
    groups: list[dict] = []
    used: set[int] = set()
    if llm is not None and with_text:
        user = "\n\n".join(f"[{i}] 標題：{n['title']}（{n['source']}，{(n['published'] or '')[:10]}）\n內文：{body[n['url']][:MAX_TEXT]}"
                           for i, n in with_text)
        if hasattr(llm, "task"):
            llm.task = "news_summary"
        try:
            raw = llm.complete(SYSTEM, user)
            res = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
        except Exception as e:  # noqa: BLE001
            res = {}
            if errors is not None:
                errors.append(f"news summary: {e!r}")
        ids = {i for i, _ in with_text}
        for g in res.get("groups") or []:
            gid = [i for i in g.get("ids") or [] if i in ids and i not in used]
            if not gid:
                continue
            items = [news[i - 1] for i in gid]
            source = "\n".join(it["title"] + "\n" + body.get(it["url"], "") for it in items)
            summary = (g.get("summary") or "").strip()
            ok = summary and not unverified(summary, source)
            groups.append({"items": items, "summary": summary if ok else None})
            used |= set(gid)
    for i, n in enumerate(news, 1):                           # 沒內文、或模型漏掉的：只有標題
        if i not in used:
            groups.append({"items": [n], "summary": None})
    return groups
