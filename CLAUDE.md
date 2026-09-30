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
- 資料層原型：**完成**；P1 每日收集（`brief/daily.py`）與摘要（`brief/digest.py`、`brief/discord.py`）已寫好，37 個測試通過
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
python -m brief.daily --db data/brief.db --send       # 每日收集 + Discord 摘要（排程跑這支）
python -m brief.digest --db data/brief.db             # 只印摘要
python -m brief.watch --db data/brief.db --dry-run     # 每站打完就發（排程每 30 分鐘跑；--dry-run 只印出）
python -m brief.backfill run --db data/brief.db       # 十年回補（可中斷續跑）
```

## 已決議（改動前先問 Raymond）

| 項目 | 決議 |
|---|---|
| 項目 | 五項全收：MS、WS、MD、WD、XD |
| 賽事層級 | 成人國際賽（對應 `brief/calendar.py` 的 `CATEGORY_LEVEL`）：Grade 1（奧運、世錦賽、湯尤盃、蘇迪曼盃）、Grade 2（World Tour 全部）、Grade 3 的 International Challenge 與 International Series、洲際個人錦標賽（亞錦賽、歐錦賽等）、綜合運動會（亞運、大英國協運動會）。**不收 Future Series**、青少年、元老賽、身障賽 |
| 賽果來源 | BWF 官網與其 JSON API，Grade 1–3 共用同一支爬蟲 |
| 選手主鍵 | BWF 選手 ID；雙打記在「組合」上，組合連到兩位選手 |
| LLM | 雲端 API，`brief/llm.py` 包一層介面。**依用途選模型**：例行（今日重點、新聞重點、暱稱擷取）`claude-sonnet-5-5`，重要（賽前分析、影片草稿、驗證差異分析）`claude-opus-5-5`；可用 `.env` 的 `LLM_MODEL_ROUTINE`／`LLM_MODEL_HEAVY` 覆寫。每次呼叫記錄用途、模型、token、估計費用到 `llm_call`（2026-09-30） |
| 推送 | Discord webhook，推到既有伺服器的指定頻道 |
| 發布 | 系統只產草稿，Raymond 審稿、錄製、發布；**不做自動發文** |
| 影片 | 不用轉播畫面；本人入鏡 + 數據圖卡。新聞只當資訊來源，草稿要改寫並附出處 |
| 爆冷 | 敗方必須是本站**種子**（種子序號依當次報名排出，不等於世界排名）。以比賽當週世界排名判斷：種子排名在前 100、輸給 100 名以外或無排名 = **大爆冷**；種子排名在前 50、輸給 50 名以外 = **爆冷**。不戰而勝不算（`digest.upset_level`，2026-09-30） |
| 日報語言 | 推到 Discord 的日報一律繁體中文（台灣用語）：項目、輪次、狀態、國家、常見賽事名稱轉中文（`brief/zh.py`）；比分與數字不變。選手名字**不音譯**，只用 `brief/player_zh.csv` 人工確認過的中文名，格式「中文（英文）」，其餘保留英文（2026-09-30） |
| 日報推送範圍 | **推送**：Grade 1（奧運、世錦賽、湯尤盃、蘇迪曼盃）、年終總決賽、Super 1000／750／500／300／100、洲際個人錦標賽（亞錦賽等）、綜合運動會（亞運等）。**IC／IS 照常抓、照常存，不推**，只推明確規則的例外（`brief/grade3.py`），每則附一行理由：① 選手曾在 Super 750 以上或 Grade 1 打進八強 ② 官方排名曾進前 30 名 ③ 中華台北選手拿到**冠軍、亞軍、季軍**（四強敗者），一站一行，賽事進行中先推「確定至少季軍／亞軍」、決賽後隔天更新。日報結尾列「今天另有 IC／IS 共 N 場，已存入資料庫」。例外不交給 LLM 判斷（2026-09-30） |
| 賽果推送時機 | **每站在當地當天最後一場打完後單獨發一則**（`brief/watch.py`，工作排程器每 30 分鐘）：標題（第 N 天、最深輪次、台灣時間幾點打完）、今日重點（LLM routine＋事實檢查）、當天全部賽果、明日看點（決賽日改列本站冠軍）。當地隔天 03:00 仍沒打完就保險發送並列「未完成：N 場」。明日賽程未公布時，之後另發一則看點（只發一次、最晚第一場開打前）。06:00 改為**晨報**：最近 24 小時新聞＋IC／IS 精選＋週一暱稱週報＋漏發提醒，全都沒有就不發（2026-09-30） |
| 明日看點 | 選場規則（`brief/preview.py`，理由由規則產生，**不用 LLM**）：① 中華台北選手 ② 雙方都在世界前 10 ③ 過去 12 個月交手且上次是決賽／四強 ④ 交手懸殊但最近弱勢方贏 ⑤ 追蹤中的台灣選手對上今年贏過他的人。最多 8 場，台灣選手優先，依台灣時間排序；時間寫「14:30（台灣）」「約 HH:MM 後，接第 N 場」「不早於…」「時間未定」。交手戰績只用資料庫（2026-09-30） |
| 暱稱 | 由系統從新聞自動收集（`brief/nickname.py`），Raymond 不手填。規則（XY配、小X、X神）＋ LLM（必須附原文證據）找候選；同篇出現全名且 ≥ 2 個來源或累積 ≥ 3 次才自動採用。採用的暱稱只用於新聞篩選，**日報提到選手不用暱稱**。每週一日報最後列本週新增與待確認的暱稱，Raymond 回覆確認或否決（2026-09-30） |
| 退休選手 | 名單「追蹤」設 N，比賽資料保留、生涯照常可查。新聞關鍵字是否保留，看退休後的新聞是否仍以羽球為主：**戴資穎保留**；**李洋移除**（現任運動部部長，新聞多為政策）；「麟洋配」暱稱保留（2026-09-30） |
| 台灣選手名單 | `config/players_zh.csv`（Raymond 維護中文名與是否追蹤；claude.ai 起草為 `players_zh_draft.csv`）。程式優先讀這份，不存在時用 `brief/player_zh.csv`。**中文名一律照表，不自行翻譯或音譯**（2026-09-30） |

## 與 claude.ai 的分工

Raymond 在 claude.ai 做調查、討論、決策與視覺化；在這裡（Claude Code）寫程式、跑程式、提交。

- **開始工作時**：讀本檔與 `docs/status.md`；**開工前先讀 `docs/notes-from-claude-ai.md` 最上面一段**（claude.ai 留下的協作方式與小結論），照做後把規則併入本段，並在該段標記「已併入」
- **收到 claude.ai 帶來的「交接單」**（`docs/handoffs/NNN-*.md`）：照單實作；單上的決定要同步寫進下方「已決議」表或 `docs/`
- **遇到需要 Raymond 做決定的事**：停下來用 A/B/C/D 問；若是需要深入討論或查資料的題目，建議他帶去 claude.ai
- **結束工作前**：在 `docs/status.md` 最上方新增一段，commit **並且 push**（Raymond 不在電腦前時，claude.ai 讀 GitHub 上的版本）
  - 格式固定：完成／發現與決定／卡住／待 Raymond 決定／下一步
  - 「待 Raymond 決定」每一題都寫：背景一句、目前暫定值或預設行為、可選方案（A/B/C/D）
  - 數字附上出處：哪個 commit、檔案或指令的輸出，讓 claude.ai 能核對
  - status.md 是唯一的進度來源；不需要另外給 claude.ai 連結或摘要
- **claude.ai 讀本機 repo 只用唯讀指令**，只會新增 `docs/notes-from-claude-ai.md` 與 `docs/handoffs/` 底下的檔案。遇到 `.git/index.lock` 錯誤、且確定沒有其他 git 程序在跑時，可以直接刪除後重試

## 工作規則

- **爬蟲禮貌**：每次請求至少間隔 2 秒（`REQUEST_GAP_SEC`），遵守 robots.txt。tournamentsoftware 的 robots.txt 禁止程式抓取，不要用；Google 新聞 RSS 也被擋，不要用；聯合新聞網的 robots.txt 禁止 Claude / ClaudeBot / GPTBot，不要用
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
