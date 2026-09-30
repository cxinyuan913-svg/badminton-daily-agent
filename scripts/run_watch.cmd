@echo off
rem 每 30 分鐘執行（Windows 工作排程器）：每站當地當天打完就發。輸出附加到 data\logs\watch.log
cd /d "%~dp0.."
if not exist data\logs mkdir data\logs
set PYTHONIOENCODING=utf-8
echo ===== %date% %time% ===== >> data\logs\watch.log
.venv\Scripts\python.exe -m brief.watch --db data\brief.db >> data\logs\watch.log 2>&1
