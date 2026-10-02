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


def test_no_control_characters():
    """2026-10-02：run_fbpost.cmd 的 \\fbpost、\\brief 被寫成換頁（^L）與倒退（^H），粉專排程從 10-01 起一直跑不起來。"""
    bad = []
    for f in _files():
        data = f.read_bytes().replace(b"\r\n", b"\n")
        if any(c < 0x20 and c not in (0x09, 0x0A) for c in data):
            bad.append(f.name)
    assert bad == []


def test_register_script_paths_exist():
    text = (ROOT / "scripts" / "register_task.ps1").read_text(encoding="utf-8-sig")
    names = re.findall(r'New-Action "([\w.]+)"', text)
    assert "run_fbpost.cmd" in names and "run_watch.cmd" in names and "run_daily.cmd" in names
    for name in names:
        assert (ROOT / "scripts" / name).exists(), name
    assert "run_backfill_news.cmd" in names
    assert "LogonType S4U" in text and "--headless" in text  # notes 10-02 17:05：背景執行不跳視窗


def test_cmd_files_use_crlf():
    """2026-10-02：LF 換行＋中文註解讓 cmd 把註解後半段當指令執行（回補排程 exit 255）。.cmd 一律 CRLF。"""
    bad = [p.name for p in (ROOT / "scripts").glob("*.cmd") if b"\n" in p.read_bytes().replace(b"\r\n", b"")]
    assert bad == []
