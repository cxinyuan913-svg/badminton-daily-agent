@echo off
rem 每天 18:00 執行（Windows 工作排程器，10-03 Raymond：固定產出統一改晚上 6 點）：
rem   1. 補抓排名（週二新排名要先進資料庫）  2. 粉專貼文（FBPOST=1 推粉專頻道）  3. 影片腳本 1 支（SCRIPT_*=1 推腳本頻道）
rem 輸出附加到 data\logs\fbpost.log
cd /d "%~dp0.."
if not exist data\logs mkdir data\logs
set PYTHONIOENCODING=utf-8
echo ===== %date% %time% ===== >> data\logs\fbpost.log
.venv\Scripts\python.exe -m brief.rankings --db data\brief.db >> data\logs\fbpost.log 2>&1
.venv\Scripts\python.exe -m brief.fbpost --db data\brief.db >> data\logs\fbpost.log 2>&1
.venv\Scripts\python.exe -m brief.script --db data\brief.db --daily >> data\logs\fbpost.log 2>&1
