#!/bin/sh
# 主機上更新程式：git pull＋重新安裝套件＋重新載入 systemd 設定（不動資料庫與 .env）
set -eu
cd "$(dirname "$0")/.."
git pull --ff-only
nice -n 10 .venv/bin/pip install -q -e .
cp deploy/systemd/badminton-*.service deploy/systemd/badminton-*.timer /etc/systemd/system/
cp deploy/logrotate.conf /etc/logrotate.d/badminton-daily-agent
systemctl daemon-reload
git log --oneline -1
