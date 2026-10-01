"""排程與部署檔不能有字串裡的 CR（notes 20:50：register_task.ps1 的 "$root\scripts<CR>un_fbpost.cmd"）。"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _files():
    for base in (ROOT / "scripts", ROOT / "deploy"):
        for p in base.rglob("*"):
            if p.is_file() and "__pycache__" not in p.parts:
                yield p


def test_no_stray_carriage_returns():
    bad = [f.name for f in _files() if f.read_bytes().replace(b"\r\n", b"").count(b"\r")]   # CRLF 換行可以，單獨的 CR 不行
    assert bad == []


def test_register_script_paths_exist():
    text = (ROOT / "scripts" / "register_task.ps1").read_text(encoding="utf-8-sig")
    names = re.findall(r'\\scripts\\([\w.]+)"', text)
    assert "run_fbpost.cmd" in names and "run_watch.cmd" in names and "run_daily.cmd" in names
    for name in names:
        assert (ROOT / "scripts" / name).exists(), name
