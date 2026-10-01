# 交接單 004：搬到雲端（與行事曆網站共用 Vultr 主機）

2026-10-01，claude.ai 撰寫。依據：Raymond 提供的主機交接文件（教練工具那邊的 session 產出）。
**Raymond 已決定（2026-10-01 11:02）**：
1. 記憶體：**先加 2 GB swap**，不升級方案（3 天後看用量再說）。
2. 時機：**等 10:30 那批試寫腳本做完、推給 Raymond 之後**，再做第 1 步以後。第 0 步檢查可以先做。

## 主機現況（摘要）
- Vultr Tokyo，1 vCPU、約 1 GB RAM、**沒有 swap**、23 GB SSD（已用約 28%），Ubuntu 26.04，Docker 已裝。
- `ssh root@66.245.221.19`，只能金鑰登入；ufw 只開 22/80/443。
- 正在跑、**絕對不要動**：`/root/coaching-record-tool`（FastAPI 容器＋Caddy 佔 80/443、每分鐘發 Discord 提醒、cron 每天 UTC 19:00 備份）。不要改它的 Caddyfile、compose、`~/.ssh/config` 的 github.com 段落、任何 .txt/.db/.env，**永遠不要** `docker compose ... down -v`。
- 完整說明在 coaching-record-tool repo 的 `spec/cloud_deployment.md`。

## 我們的需求為什麼適合這台
- 我們**不需要對外網站**（全部是排程工作＋Discord webhook），所以不碰 Caddy、不開 port、不用子網域。
- 資料庫 `data/brief.db` 約 105 MB；每次工作是短時間的 Python 程序。
- 唯一風險是記憶體：1 GB、沒有 swap。

## 第 0 步：檢查（可以先做，唯讀）
用 Raymond 電腦上的 SSH 金鑰連線，只跑唯讀指令，結果貼在 status.md：
```
nproc; free -h; swapon --show; df -h /; docker stats --no-stream; crontab -l; python3 --version
curl -s -o /dev/null -w "%{http_code}\n" "https://extranet-lv.bwfbadminton.com/api/vue-rankingweek?rankId=2"
curl -s -o /dev/null -w "%{http_code}\n" "https://www.cna.com.tw/list/aspt.aspx"
curl -s -o /dev/null -w "%{http_code}\n" "https://www.nownews.com/"
curl -s -o /dev/null -w "%{http_code}\n" "https://bwfbadminton.com/"
```
重點：BWF API、中央社、NOWnews 從 Vultr 東京的 IP 打不打得通（雲端 IP 有時會被擋）。打不通就停下來回報。

## 第 1–6 步（等 Raymond 決定後再做）
1. **記憶體**：依 Raymond 決定，加 2 GB swap 檔（`fallocate -l 2G /swapfile` …，寫進 /etc/fstab，`vm.swappiness=10`）或升級方案。
2. **部署方式：不用 Docker**（避免 `docker build` 吃記憶體）。
   - repo 是公開的，用 HTTPS clone，**不需要部署金鑰**：`git clone https://github.com/cxinyuan913-svg/badminton-daily-agent.git /root/badminton-daily-agent`
   - Python venv 在專案資料夾內；`.env` 用 scp 從 Raymond 電腦複製（不進 git、權限 600）。
   - 時區：主機是 UTC，程式內部已用台北時間計算；排程時間換算見下。
3. **排程：systemd timer**（每個都加 `MemoryMax=400M`、`Nice=10`，避免拖慢教練工具）
   | 工作 | 原本 Windows | 主機（UTC） |
   |---|---|---|
   | `brief.watch` | 每 30 分鐘 | 每 30 分鐘 |
   | `brief.daily --send`（晨報） | 台北 06:00 | 每天 22:00 UTC |
   | 每週排名（含排名更新腳本） | 週二 | 依現在的設定換算 |
   - 避開教練工具備份時間（UTC 19:00）。
   - 同一時間只跑一個我們的工作（用 `flock` 鎖）。
4. **切換（最重要：不能兩邊同時推送）**
   1. 停用 Raymond 電腦上的 Windows 工作排程器（watch、daily、weekly）。
   2. 把最新的 `data/brief.db` scp 到主機（先在本機用 `sqlite3 .backup` 產生一致的副本再傳）。
   3. 主機手動跑一次 `brief.watch` 與 `brief.daily`（不加 `--send`）確認正常，再啟用 timer。
   4. 隔天確認 Discord 三個頻道都有正常訊息，且沒有重複。
5. **備份與紀錄**：每天 UTC 20:00 用 `sqlite3 .backup` 備份 `brief.db`，保留 7 天；log 放 `data/logs/`，設 logrotate（保留 14 天）。
6. **之後的開發流程**（寫進 CLAUDE.md）
   - 開發照舊在 Raymond 電腦；push 到 GitHub 後，主機 `git pull` 生效（之後可加一支 `scripts/deploy.sh`）。
   - **正式資料庫在主機上**。本機要測試用資料時，從主機下載一份副本；**不要把本機資料庫傳回主機覆蓋**。
   - 長時間的回補（例如十年回補）在本機跑，跑完再把結果合併到主機，避免在 1 GB 主機上跑好幾個小時。

## 驗收
- 主機連續 3 天：watch 每 30 分鐘有紀錄、晨報準時、沒有重複推送、教練工具的提醒照常。
- `free -h` 與 `docker stats` 前後對照貼在 status.md（確認沒有排擠教練工具）。
- 記一條 `docs/highlights.md`：雲端部署（共用主機、資源限制、無停機切換）。

完成後：更新 status.md、CLAUDE.md、`docs/plan.md`（P4）、commit、push。
