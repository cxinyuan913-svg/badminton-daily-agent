@echo off
rem 每週一 12:00 執行（Windows 工作排程器）：最新一份備份 gzip 後推到主機 bda-vultr:/root/badminton-backups/，保留 8 份（notes 10-02 06:35）
cd /d "%~dp0.."
if not exist data\logs mkdir data\logs
set PYTHONIOENCODING=utf-8
echo ===== %date% %time% ===== >> data\logs\offsite.log
.venv\Scripts\python.exe scripts\offsite_backup.py data\backups >> data\logs\offsite.log 2>&1
