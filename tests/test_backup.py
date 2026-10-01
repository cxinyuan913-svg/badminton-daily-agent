import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from backup_db import backup  # noqa: E402


def test_backup_copies_and_keeps_n(tmp_path):
    db = tmp_path / "brief.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE t (x)")
    con.execute("INSERT INTO t VALUES (1)")
    con.commit()
    con.close()
    out = tmp_path / "backup"
    for name in ["brief-20260101.db", "brief-20260102.db", "brief-20260103.db"]:
        (out.mkdir(exist_ok=True), (out / name).write_bytes(b""))
    target = backup(str(db), str(out), 2)
    assert sqlite3.connect(target).execute("SELECT x FROM t").fetchone() == (1,)
    assert len(list(out.glob("brief-*.db"))) == 2 and target.exists()
