@echo off
rem News backfill (notes 2026-10-02 07:25 item 1): hourly trigger, one instance at a time, resumes; finished sources exit at once.
rem Usage: run_backfill_news.cmd SOURCE   (SOURCE = bwf or tsna). Log: data\logs\backfill_news_SOURCE.log
cd /d "%~dp0.."
if not exist data\logs mkdir data\logs
set PYTHONIOENCODING=utf-8
.venv\Scripts\python.exe -m brief.news_backfill --db data\brief.db --source %1 --since 2024-10-02 >> data\logs\backfill_news_%1.log 2>&1
