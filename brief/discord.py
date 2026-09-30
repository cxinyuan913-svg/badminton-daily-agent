"""Discord webhook 推送。

Discord 單則訊息上限 2000 字，長摘要依行切成多則。遇到 429（太頻繁）照 retry_after 等待後重送。
webhook 網址放在 .env（DISCORD_WEBHOOK_DAILY、DISCORD_WEBHOOK_ALERTS），不進版控。
"""
from __future__ import annotations

import os
import time
from pathlib import Path

LIMIT = 2000
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def webhook(name: str, env_file: Path = ENV_FILE) -> str:
    """先看環境變數，再看 .env。沒設定就報錯，不要靜默略過。"""
    value = os.environ.get(name)
    if not value and env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, sep, val = line.partition("=")
            if sep and key.strip() == name:
                value = val.strip().strip('"').strip("'")
    if not value:
        raise RuntimeError(f"沒有設定 {name}：請在 .env 填入 Discord webhook 網址")
    return value


def split_message(text: str, limit: int = LIMIT) -> list[str]:
    """依行切分；單行超過上限時硬切。"""
    chunks, cur = [], ""
    for line in text.splitlines():
        while len(line) > limit:
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = f"{cur}\n{line}" if cur else line
        if len(candidate) > limit:
            chunks.append(cur)
            cur = line
        else:
            cur = candidate
    if cur:
        chunks.append(cur)
    return chunks


def send(url: str, text: str, session=None) -> int:
    """推送並回傳則數。任何一則失敗就丟例外（呼叫端不應標記為已推送）。"""
    if session is None:
        import requests
        session = requests.Session()
    chunks = split_message(text)
    for chunk in chunks:
        for attempt in range(3):
            r = session.post(url, json={"content": chunk, "allowed_mentions": {"parse": []}}, timeout=30)
            if r.status_code == 429 and attempt < 2:
                time.sleep(float(r.json().get("retry_after", 2)))
                continue
            r.raise_for_status()
            break
        time.sleep(0.5)
    return len(chunks)
