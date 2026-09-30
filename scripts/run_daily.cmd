@echo off
rem 每日排程入口（Windows 工作排程器每天 06:00 執行）。輸出附加到 data\logs\daily.log
cd /d "%~dp0.."
if not exist data\logs mkdir data\logs
set PYTHONIOENCODING=utf-8
echo ===== %date% %time% ===== >> data\logs\daily.log
.venv\Scripts\python.exe -m brief.daily --db data\brief.db --send >> data\logs\daily.log 2>&1
