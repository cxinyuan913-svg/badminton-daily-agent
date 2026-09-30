# 羽球日報 Agent

每天自動收集國際羽球賽果與新聞，記住選手生涯，產出一分鐘 IG 影片草稿並推送到 Discord。

文件：[開發計畫](docs/plan.md) · [資料來源](docs/data-sources.md) · [給 Claude Code 的說明](CLAUDE.md)

## 目前進度：資料層原型

2026-09-30 開發前實測後寫出的第一版。Grade 1、2、3 的賽果都從 BWF 官網同一套 API 取得。

## 檔案

| 檔案 | 內容 |
|---|---|
| `brief/schema.sql` | 資料表：賽事、選手、組合、比賽、每局比分、排名快照，以及 `player_match` 檢視表 |
| `brief/crawler.py` | 爬一站賽事的所有比賽並寫入 SQLite；重跑不會重複寫入 |
| `brief/rankings.py` | 世界排名週快照（API 只留約 60 週，須每週執行） |
| `brief/live.py` | 取得進行中賽事，每日排程的入口 |
| `brief/scanner.py` | 掃描賽事 ID、判斷 Grade 3 層級、匯出 IC / IS 清單 |
| `tests/` | 10 個測試，用真實 API 回應當測試資料 |
| `tests/fixtures/` | North Harbour International 2026-09-30 的真實回應（節錄 8 場） |

## 安裝與執行

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m pytest -q                                  # 跑測試
python -m brief.crawler 5766 --db data/brief.db      # 抓 North Harbour International 2026
python -m brief.crawler 3600 --db data/brief.db      # 抓 Myanmar International Series 2019
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

- 團體賽（湯尤盃、蘇迪曼盃）的外層比賽會跳過，個別對戰在 `matches` 欄位內，之後處理。
- 賽事的 Grade 與 level 欄位要由 `scanner.py` 或 World Tour 賽程補上。
- 排名快照爬蟲尚未撰寫。
