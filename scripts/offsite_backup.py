"""異地備份（notes 10-02 06:35 第 3 點）：每週一 12:00（Windows 排程）把本機最新一份備份 gzip 後 scp 到主機。

  主機 `ssh bda-vultr`（本專案專用金鑰）的 /root/badminton-backups/，保留 8 份；主機只當備份櫃，不跑任何排程。
  上傳前先 PRAGMA integrity_check；任何一步失敗 → 推告警（DISCORD_WEBHOOK_ALERTS）。

  python scripts/offsite_backup.py data/backups
"""
from __future__ import annotations

import gzip
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

HOST = "bda-vultr"
REMOTE_DIR = "/root/badminton-backups"
KEEP = 8


def latest_backup(folder: Path) -> Path:
    files = sorted(folder.glob("brief-*.db"))
    if not files:
        raise FileNotFoundError(f"{folder} 裡沒有 brief-*.db 備份")
    return files[-1]


def check(db: Path) -> None:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        result = con.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        con.close()
    if result != "ok":
        raise RuntimeError(f"{db.name} integrity_check：{result}")


def push(db: Path) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        gz = Path(tmp) / (db.name + ".gz")
        with db.open("rb") as src, gzip.open(gz, "wb") as dst:
            shutil.copyfileobj(src, dst)
        ssh = ["ssh", "-o", "BatchMode=yes", HOST]
        subprocess.run(ssh + [f"mkdir -p {REMOTE_DIR}"], check=True, capture_output=True, timeout=120)
        subprocess.run(["scp", "-q", "-o", "BatchMode=yes", str(gz), f"{HOST}:{REMOTE_DIR}/"], check=True,
                       capture_output=True, timeout=1800)
        prune = f"cd {REMOTE_DIR} && ls -1 brief-*.db.gz | sort | head -n -{KEEP} | xargs -r rm -f && ls -1 | wc -l"
        out = subprocess.run(ssh + [prune], check=True, capture_output=True, text=True, timeout=120)
        return f"{gz.name}（{gz.stat().st_size / 1e6:.1f} MB gzip）→ {HOST}:{REMOTE_DIR}，主機現有 {out.stdout.strip()} 份"


def main():
    folder = Path(sys.argv[1] if len(sys.argv) > 1 else "data/backups")
    try:
        db = latest_backup(folder)
        check(db)
        print(push(db))
    except Exception as e:  # noqa: BLE001
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from brief import discord
        discord.send(discord.webhook("DISCORD_WEBHOOK_ALERTS"), f"**異地備份失敗**：{e!r}"[:1800])
        print(f"失敗：{e!r}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
