"""全測試共用：不讓測試讀到本機 .env 的腳本開關（2026-10-01：SCRIPT_*=dry 時 watch 測試真的呼叫了 LLM API）。"""
import pytest


@pytest.fixture(autouse=True)
def _no_script_generation(monkeypatch, tmp_path):
    from brief import script
    real_env = script._env
    monkeypatch.setattr(script, "_env", lambda k: None if k.startswith("SCRIPT_") or k == "DISCORD_WEBHOOK_SCRIPTS" else real_env(k))
    monkeypatch.setattr(script, "OUT_DIR", tmp_path / "scripts")
    monkeypatch.setenv("NEWS_VECTORS", "0")   # 交接單 007：其他測試不載入真的 BGE-M3（向量庫自己的測試用假 embedding）
