# CLAUDE.md — 羽球日報 Agent

給接手的 Claude Code session 看的專案說明。每次開始工作前先讀這份，再看 `docs/`。

## 這是什麼

專案名稱：`badminton-daily-agent`（資料夾與 GitHub repo 同名；Python 套件名稱仍是 `brief`）

每天自動收集國際羽球賽果與新聞，記住每位選手的歷史，產出可以直接錄製的一分鐘 IG 影片草稿，推送到 Discord。
同時是負責人 Raymond 的 AI 工程師求職主作品，所以程式品質、測試、架構說明都要能拿去面試展示。

- 負責人：Raymond（前職業羽球雙打選手、羽球教練，轉職 AI 工程師）
- 溝通語言：繁體中文。需要他做決定時，給 A/B/C/D 選項，不要開放式提問
- 他懂羽球遠比懂程式多：領域問題（賽制、術語、選手）以他的說法為準

## 目前狀態（2026-09-30）

- P0 資料來源調查：**完成**，細節在 `docs/data-sources.md`
- 資料層原型：**完成**，17 個測試通過
  - `brief/crawler.py`：賽果（含團體賽拆單場）
  - `brief/rankings.py`：世界排名週快照
  - `brief/live.py`：進行中賽事清單（每日排程的入口）
  - `brief/scanner.py`：賽事 ID 掃描與 Grade 3 層級判斷
  - `brief/schema.sql`：資料表
  - `brief/calendar.py`：年度賽程 → 追蹤範圍內的賽事與層級（**找賽事的主要方法**）
- 2016–2026 賽事清單：用 `python -m brief.calendar --from 2016 --to 2026 --csv data/tournaments.csv` 產生（11 個請求）。
  統計與驗證見 `docs/data-sources.md`
- 下一步：P1（每日排程 + Discord 摘要），見 `docs/plan.md`
- 第一個實戰目標：HSBC BWF World Tour Finals 2026（12/9–13 杭州）的賽前分析

## 指令

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env
python -m pytest -q
python -m brief.crawler 5766 --db data/brief.db       # 抓一站
python -m brief.calendar --from 2016 --to 2026 --db data/brief.db   # 建立賽事清單與層級
python -m brief.rankings --db data/brief.db           # 補齊排名快照（API 只留約 60 週）
python -m brief.live                                  # 列出進行中賽事
```

## 已決議（改動前先問 Raymond）

| 項目 | 決議 |
|---|---|
| 項目 | 五項全收：MS、WS、MD、WD、XD |
| 賽事層級 | 成人國際賽（對應 `brief/calendar.py` 的 `CATEGORY_LEVEL`）：Grade 1（奧運、世錦賽、湯尤盃、蘇迪曼盃）、Grade 2（World Tour 全部）、Grade 3 的 International Challenge 與 International Series、洲際個人錦標賽（亞錦賽、歐錦賽等）、綜合運動會（亞運、大英國協運動會）。**不收 Future Series**、青少年、元老賽、身障賽 |
| 賽果來源 | BWF 官網與其 JSON API，Grade 1–3 共用同一支爬蟲 |
| 選手主鍵 | BWF 選手 ID；雙打記在「組合」上，組合連到兩位選手 |
| LLM | 雲端 API；程式要包一層介面，之後可換模型 |
| 推送 | Discord webhook，推到既有伺服器的指定頻道 |
| 發布 | 系統只產草稿，Raymond 審稿、錄製、發布；**不做自動發文** |
| 影片 | 不用轉播畫面；本人入鏡 + 數據圖卡。新聞只當資訊來源，草稿要改寫並附出處 |

## 與 claude.ai 的分工

Raymond 在 claude.ai 做調查、討論、決策與視覺化；在這裡（Claude Code）寫程式、跑程式、提交。

- **開始工作時**：讀本檔與 `docs/status.md`
- **收到 claude.ai 帶來的「交接單」**：照單實作；單上的決定要同步寫進下方「已決議」表或 `docs/`
- **遇到需要 Raymond 做決定的事**：停下來用 A/B/C/D 問；若是需要深入討論或查資料的題目，建議他帶去 claude.ai
- **結束工作前**：在 `docs/status.md` 最上方新增一段（日期、完成、卡住、待決定、下一步），並提交

## 工作規則

- **爬蟲禮貌**：每次請求至少間隔 2 秒（`REQUEST_GAP_SEC`），遵守 robots.txt。tournamentsoftware 的 robots.txt 禁止程式抓取，不要用；Google 新聞 RSS 也被擋，不要用
- **事實正確優先**：草稿裡每個比分、名字、數字都要能回資料庫查證。不確定的句子標出來給 Raymond 確認，不要自動放行
- **冪等**：爬蟲重跑只更新、不重複寫入。新增寫入邏輯要附重跑測試
- **測試資料用真實回應**：新 API 或新欄位，先存一份真實回應到 `tests/fixtures/`，再寫解析
- **不進版控**：`.env`、`*.db`、`data/`
- commit 訊息用中文或英文皆可，一個 commit 做一件事

## 已知陷阱

- 舊賽事（例如 2019）的 `matchStatus` 是 `null`，完成與否要看 `winner`（見 `crawler.is_finished`）
- 進行中賽事的狀態有 F（Finished）、O（Off court）、C（On Court）、I（In Progress）、N（未開打），只收 F、O
- 退賽的 `scoreStatusValue` 是 `Retired`，照樣寫入
- 選手的 `nameShort` 可能帶結尾空白，例如 `"TEO W J "`
- 團體賽的外層 `isTeamMatch=true` 記進 `team_tie`，單場在 `matches` 欄位；單場的 `eventName` 是賽事名（例如 Uber Cup），項目要看 `matchTypeValue`
- **排名 API 只保留最近約 60 週**（2026-09 實測最早 2025-08-12）。`rankings.py` 必須每週跑，否則歷史會永久遺失。更早比賽的「爆冷」只能用種子或頒獎台上的排名估計
- 年度賽程 API `vue-grouped-year-tournaments` **不要加 `category[]` 篩選**（結果不可靠）；不加參數拿全年，再用每筆的 `category` 名稱判斷
- 賽事 ID 會提前分配（5901 以後是 2027–2028 年），找新賽事看日期，不看 ID
- 頒獎台積分在 2022–2024 年大多是空的，不能拿來判斷層級
- `vue-current-live` 的 `tournament_category_id` 與分類 API 的 id 是不同編號，不要混用
- 賽事頁日期只有「日 月」，年份要從賽事名稱取；跨年賽事已處理
- 冠軍積分判斷層級只適用 2018 年新制之後：4000=IC、2500=IS、1700=FS
