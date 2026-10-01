#!/bin/sh
# 主機（Linux）排程入口：同一時間只跑一個本專案的工作（flock），輸出附加到 data/logs/<工作>.log
#   run_job.sh watch | daily | backup
set -eu
cd "$(dirname "$0")/.."
mkdir -p data/logs
export PYTHONIOENCODING=utf-8
job="$1"
case "$job" in
  watch)  cmd=".venv/bin/python -m brief.watch --db data/brief.db" ;;
  daily)  cmd=".venv/bin/python -m brief.daily --db data/brief.db --send" ;;
  backup) cmd=".venv/bin/python scripts/backup_db.py data/brief.db data/backup 7" ;;
  *) echo "unknown job: $job" >&2; exit 2 ;;
esac
log="data/logs/$job.log"
echo "===== $(date -u '+%F %T') UTC =====" >> "$log"
# 最多等 15 分鐘拿鎖（晨報不能因為 watch 正在跑就被跳過）
exec flock -w 900 /run/lock/badminton-daily-agent.lock $cmd >> "$log" 2>&1
