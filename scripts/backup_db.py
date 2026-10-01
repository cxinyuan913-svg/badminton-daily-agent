"""SQLite 一致備份（主機沒有 sqlite3 指令，用 Python 內建 backup API）。保留最近 N 份。

  python scripts/backup_db.py data/brief.db data/backup 7
"""
import datetime as dt
import sqlite3
import sys
from pathlib import Path


def backup(db: str, out_dir: str, keep: int) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"brief-{dt.datetime.now(dt.timezone.utc):%Y%m%d}.db"
    src, dst = sqlite3.connect(db), sqlite3.connect(target)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    for old in sorted(out.glob("brief-*.db"))[:-keep]:
        old.unlink()
    return target


if __name__ == "__main__":
    print(backup(sys.argv[1], sys.argv[2], int(sys.argv[3])))
