# badminton-daily-agent（羽球日報 Agent）

每天自動收集國際羽球賽果與新聞，記住選手生涯，產出一分鐘 IG 影片草稿並推送到 Discord。

文件：[開發計畫](docs/plan.md) · [資料來源](docs/data-sources.md) · [給 Claude Code 的說明](CLAUDE.md)

## 目前進度：P1 每日收集與摘要

資料層原型完成，2016–2026 賽事清單、排名快照已建立；每日收集與 Discord 摘要已寫好，排程待設定。Grade 1、2、3 的賽果都從 BWF 官網同一套 API 取得。

## 檔案

| 檔案 | 內容 |
|---|---|
| `brief/schema.sql` | 資料表：賽事、選手、組合、比賽、每局比分、排名快照，以及 `player_match` 檢視表 |
| `brief/crawler.py` | 爬一站賽事的所有比賽並寫入 SQLite；重跑不會重複寫入 |
| `brief/rankings.py` | 世界排名週快照（API 只留約 60 週，須每週執行） |
| `brief/live.py` | 取得進行中賽事，每日排程的入口 |
| `brief/calendar.py` | 年度賽程 → 追蹤範圍內的賽事與層級（找賽事的主要方法） |
| `brief/scanner.py` | 掃描賽事 ID、判斷 Grade 3 層級（備援） |
| `brief/daily.py` | 每日收集：賽程 → 進行中賽事 → 最近兩天賽果 → 缺少的排名週次，紀錄寫入 `crawl_run` |
| `brief/digest.py` | 每日文字摘要：八強以後全列，早期輪次只列爆冷與台灣選手 |
| `brief/discord.py` | Discord webhook 推送（2000 字切分、429 重試） |
| `tests/` | 37 個測試，用真實 API 回應當測試資料 |
| `tests/fixtures/` | 賽程、進行中賽事、賽果、團體賽、排名的真實回應節錄 |

## 安裝與執行

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m pytest -q                                  # 跑測試
python -m brief.crawler 5766 --db data/brief.db      # 抓 North Harbour International 2026
python -m brief.crawler 3600 --db data/brief.db      # 抓 Myanmar International Series 2019
python -m brief.calendar --from 2016 --to 2026 --db data/brief.db   # 建立賽事清單
python -m brief.rankings --db data/brief.db          # 補齊排名快照
python -m brief.daily --db data/brief.db --send      # 每日收集 + 推送摘要（需在 .env 設定 webhook）
python -m brief.digest --db data/brief.db            # 只印出摘要，不推送
python -m brief.scanner scan --from 2400 --to 5900 --db data/brief.db
python -m brief.scanner classify --db data/brief.db
python -m brief.scanner export grade3.csv --db data/brief.db
```

每次請求間隔 2 秒。全掃約 3,500 個 ID，大約需要兩小時，建議放在夜間執行。

## 資料來源

- 賽事頁：`https://bwfbadminton.com/tournament/{id}/x/`
  取名稱、API 用的 GUID、比賽日期（`<div class="live-date">`）
- 每日賽果：`https://extranet-lv.bwfbadminton.com/api/tournaments/day-matches?tournamentCode={GUID}&date=YYYY-MM-DD&order=2&court=0`
- 頒獎台：`https://extranet-lv.bwfbadminton.com/api/vue-tournament-podium?...&tmtId={id}&podiumEventCode=1`
  冠軍積分 4000 = International Challenge、2500 = International Series、1700 = Future Series

## 實測重點

- 2019 年的 Grade 3 也有逐場比分與選手 ID。Myanmar International Series 2019：6 天共 126 場，全部可寫入；男單決賽與頒獎台一致。
- 舊賽事的 `matchStatus` 是 `null`，所以完成與否以 `winner` 判斷（已修正並加測試）。
- 退賽的 `scoreStatusValue` 是 `Retired`，照樣寫入並保留狀態。
- 雙打組合以兩個選手 ID 排序後當唯一鍵，查個人生涯時可以帶出所有搭檔。

## 已知待辦

- 每日排程（06:00 Asia/Taipei）的執行位置待決定
- 新聞收集、LLM 摘要改寫
- 只出現在排名裡的選手沒有姓名（排名 API 只給 slug）
