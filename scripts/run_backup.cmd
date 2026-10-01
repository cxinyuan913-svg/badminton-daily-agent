@echo off
rem 每天 04:00 執行（Windows 工作排程器）：data\brief.db 一致備份到 data\backups\，保留 7 份（notes 10-02 06:35）
cd /d "%~dp0.."
if not exist data\logs mkdir data\logs
set PYTHONIOENCODING=utf-8
echo ===== %date% %time% ===== >> data\logs\backup.log
.venv\Scripts\python.exe scripts\backup_db.py data\brief.db data\backups 7 >> data\logs\backup.log 2>&1
