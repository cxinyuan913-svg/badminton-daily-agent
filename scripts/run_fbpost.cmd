@echo off
rem 每天 07:00 執行（Windows 工作排程器）：FB 粉專貼文草稿。FBPOST=1 才推粉專頻道，否則只寫 data\posts\。輸出附加到 data\logs\fbpost.log
cd /d "%~dp0.."
if not exist data\logs mkdir data\logs
set PYTHONIOENCODING=utf-8
echo ===== %date% %time% ===== >> data\logs\fbpost.log
.venv\Scripts\python.exe -m brief.fbpost --db data\brief.db >> data\logs\fbpost.log 2>&1
