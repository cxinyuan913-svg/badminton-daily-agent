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

最新進度一律看 `docs/status.md` 最上面一段；這裡只列大方向。

- P0 資料來源調查：**完成**（`docs/data-sources.md`）
- P1 每日收集＋推送：**程式完成、已上線**，開始累積關卡「連續 7 天沒有漏抓」
  - `brief.watch`（每 30 分鐘）每站當地當天打完就發；`brief.daily`（06:00）收集＋晨報
  - 日報中文化、爆冷規則、IC／IS 例外、明日看點、今日重點（LLM＋事實檢查）、暱稱收集
- 交接單 002（十年回補＋排名重建）：**十年回補完成**；排名重建與驗證完成後收尾（`docs/ranking-validation.md`），日報改用 2017 年起的**官方歷史排名**
- 測試 147 個（`python -m pytest -q`），fixture 全是真實回應
- 模組一覽見 `README.md`
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
| 賽事層級 | 成人國際賽（對應 `brief/calendar.py` 的 `CATEGORY_LEVEL`）：Grade 1（奧運、世錦賽、湯尤盃、蘇迪曼盃）、Grade 2（World Tour 全部）、Grade 3 的 International Challenge 與 International Series、洲際個人錦標賽、綜合運動會（亞運、大英國協運動會）、**洲際團體錦標賽**（`CONT_TEAM`）、**世大運**（`FISU`，個人與團體賽依規章 §7.1／7.2 計分）。**不收 Future Series**（23:10 取消 FS 回補）。不收青少年、元老賽、身障賽。大英國協運動會不在 V6.0 計分清單，**不計分**（2026-09-30 22:35／22:45） |
| 賽果來源 | BWF 官網與其 JSON API，Grade 1–3 共用同一支爬蟲 |
| 選手主鍵 | BWF 選手 ID；雙打記在「組合」上，組合連到兩位選手 |
| LLM | 雲端 API，`brief/llm.py` 包一層介面。**依用途選模型**：例行（今日重點、新聞重點、暱稱擷取）`claude-sonnet-5-5`，重要（賽前分析、影片草稿、驗證差異分析）`claude-opus-5-5`；可用 `.env` 的 `LLM_MODEL_ROUTINE`／`LLM_MODEL_HEAVY` 覆寫。每次呼叫記錄用途、模型、token、估計費用到 `llm_call`（2026-09-30） |
| 推送 | Discord webhook，推到既有伺服器的指定頻道 |
| 發布 | 系統只產草稿，Raymond 審稿、錄製、發布；**不做自動發文** |
| 影片 | 不用轉播畫面；本人入鏡 + 數據圖卡。新聞只當資訊來源，草稿要改寫並附出處 |
| 爆冷 | 敗方必須是本站**種子**（種子序號依當次報名排出，不等於世界排名）。以比賽當週世界排名判斷：種子排名在前 100、輸給 100 名以外或無排名 = **大爆冷**；種子排名在前 50、輸給 50 名以外 = **爆冷**。不戰而勝不算（`digest.upset_level`，2026-09-30） |
| 今日重點評語 | 「逆轉」「三局大戰」這類可由比分驗證的詞可以用；**「爆冷／冷門」只能用在 `upset_level` 判定的場次**，事實檢查會擋下違反的輸出（`llm.unlicensed_upsets`）（2026-09-30 22:45）。句子沒寫名字時：寫兩個排名且對得上同一場、或寫「項目＋決賽／冠軍」且該項目決賽有判定，也放行（2026-10-01 13:35 B） |
| 日報語言 | 推到 Discord 的日報一律繁體中文（台灣用語）：項目、輪次、狀態、國家、常見賽事名稱轉中文（`brief/zh.py`）；比分與數字不變。選手名字**不音譯**，只用 `brief/player_zh.csv` 人工確認過的中文名，格式「中文（英文）」，其餘保留英文（2026-09-30） |
| 日報推送範圍 | **推送**：Grade 1（奧運、世錦賽、湯尤盃、蘇迪曼盃）、年終總決賽、Super 1000／750／500／300／100、洲際個人錦標賽（亞錦賽等）、綜合運動會（亞運等）。**IC／IS 照常抓、照常存，不推**，只推明確規則的例外（`brief/grade3.py`），每則一行理由＋該選手當天最後一場（22:45）：① 選手曾在 Super 750 以上或 Grade 1 打進八強 ② 官方排名曾進前 30 名 ③ 中華台北選手拿到**冠軍、亞軍、季軍**（四強敗者），一站一行，賽事進行中先推「確定至少季軍／亞軍」、決賽後隔天更新。日報結尾列「今天另有 IC／IS 共 N 場，已存入資料庫」。例外不交給 LLM 判斷（2026-09-30） |
| 空檔週 | 某一 BWF 週（週一到週日）**完全沒有推送層級賽事**（賽期與這週重疊就算有）時，這週的 IC／IS 升格，由 `brief.watch` 每站當地當天打完就發：標題標層級（「…｜IC｜第 3 天 16 強」），八強以前只列中華台北與爆冷場次、八強起全列，最後一行「另有 N 場未列」；晨報不再重複列。Future Series 不升格（`grade3.quiet_week`，2026-09-30 23:15） |
| 世界排名來源 | **官方歷史排名**：`player/ranking/publication/weeks` 取週次、`vue-rankingtable` 取每週前 100 名，2017-01 起存進 `ranking_snapshot`；日報的排名一律用官方資料。自行重建（`ranking_estimate`、`validate`、`docs/ranking-validation.md`）**收尾不再加功能**，保留作驗證與方法展示。規章 PDF 在 `docs/regulations/` 備查（不進版控）（2026-09-30 23:10） |
| 賽果推送時機 | **每站在當地當天最後一場打完後單獨發一則**（`brief/watch.py`，工作排程器每 30 分鐘）：標題（第 N 天、最深輪次、台灣時間幾點打完）、今日重點（LLM routine＋事實檢查）、當天全部賽果、明日看點（決賽日改列本站冠軍）。當地隔天 03:00 仍沒打完就保險發送並列「未完成：N 場」。明日賽程未公布時，之後另發一則看點（只發一次、最晚第一場開打前）。06:00 改為**晨報**：最近 24 小時新聞＋IC／IS 精選＋週一暱稱週報＋漏發提醒，全都沒有就不發（2026-09-30） |
| 明日看點 | 選場規則（`brief/preview.py`，理由由規則產生，**不用 LLM**）：① 中華台北選手 ② 雙方都在世界前 10 ③ 過去 12 個月交手且上次是決賽／四強 ④ 交手懸殊但最近弱勢方贏 ⑤ 追蹤中的台灣選手對上今年贏過他的人。最多 8 場，台灣選手優先，依台灣時間排序；時間寫「14:30（台灣）」「約 HH:MM 後，接第 N 場」「不早於…」「時間未定」。交手戰績只用資料庫（2026-09-30） |
| 暱稱 | 由系統從新聞自動收集（`brief/nickname.py`），Raymond 不手填。規則（XY配、小X、X神）＋ LLM（必須附原文證據）找候選；同篇出現全名且 ≥ 2 個來源或累積 ≥ 3 次才自動採用。採用的暱稱只用於新聞篩選，**日報提到選手不用暱稱**。每週一日報最後列本週新增與待確認的暱稱，Raymond 回覆確認或否決（2026-09-30） |
| 退休選手 | 名單「追蹤」設 N，比賽資料保留、生涯照常可查。新聞關鍵字是否保留，看退休後的新聞是否仍以羽球為主：**戴資穎保留**；**李洋移除**（現任運動部部長，新聞多為政策）；「麟洋配」暱稱保留（2026-09-30） |
| 台灣選手名單 | `config/players_zh.csv`（Raymond 維護中文名與是否追蹤；claude.ai 起草為 `players_zh_draft.csv`）。程式優先讀這份，不存在時用 `brief/player_zh.csv`。**中文名一律照表，不自行翻譯或音譯**（2026-09-30） |
| 外國選手譯名 | `brief/foreign_names.py`：**只用台灣媒體原文**（中央社、NOWnews）「中文（English）」當證據；華裔選手可用原文漢字＋拼音對回 BWF 名字（姓氏、分隔字、國家規則）。2 個不同來源才 confirmed，衝突以中央社為準；Raymond 填的鎖定不覆蓋。**不用 LLM 或拼音自己造譯名**；沒有譯名就用英文（交接單 003，2026-10-01） |
| 影片腳本 | `brief/script.py` ＋ `brief/storylines.py`（交接單 003、notes 10:15／10:20／10:25／10:30／10:40，2026-10-01）。風格：決賽日 1 快報、2 單一故事（heavy）、3 台灣視角（有台灣選手才產生）、4 數據型（觀察中，故事不足 3 則不產生）（routine；13:35 A），上限 US$0.40／站；比賽日台灣視角＋單一故事（分數 ≥ `DAILY_STORY_MIN`=6），八強起加快報（當天輪次），用 routine；週二新一週排名存好後產生排名更新（heavy）。每份 3 個標題、口播 200–320 字。**只用官方排名**（估算或查不到就不寫排名）。事實檢查不過重試一次，再不過只丟那一份並告警。`.env` 開關 `SCRIPT_FINAL`／`SCRIPT_DAILY`／`SCRIPT_WEEKLY`：**目前三個都是 `dry`**（13:35 B，Raymond 回饋試寫後再改 1）；空白＝不產生、不花錢，`dry` 只寫檔 `data/scripts/`，`1` 推到 **`DISCORD_WEBHOOK_SCRIPTS`（腳本專用頻道，不推日報頻道）**。週一晨報列「上週腳本費用」，前一天超過 US$0.5 告警 |
| 獎牌用詞 | 只有頒獎牌的賽事（奧運、世錦賽、綜合運動會、洲際錦標賽、團體世界賽、世大運）寫金／銀／銅牌；World Tour、IC、IS 寫冠軍、亞軍、四強（2026-10-01 試寫發現後 Claude Code 定的規則，Raymond 可改） |
| 雲端主機 | 與教練工具共用 Vultr 東京主機（交接單 004）。**用 `ssh bda-vultr` 連主機**（本專案專用金鑰，別名在 `~/.ssh/config`）；只動 `/root/badminton-daily-agent` 與 `badminton-*` systemd 單元，交接單 004「不要動」一節照守。排程：`badminton-watch.timer`（每 30 分）、`badminton-daily.timer`（UTC 22:00＝台北 06:00）、`badminton-backup.timer`（UTC 20:00，保留 7 份）；入口 `scripts/run_job.sh`（flock 同時只跑一個、`MemoryMax=400M`、`Nice=10`）；更新程式 `scripts/deploy.sh`。**切換前 timer 不啟用**，本機 Windows 排程照跑（2026-10-01 13:45） |

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
  - **有亮點就記進 `docs/highlights.md`**（求職作品素材）：日期、問題、怎麼發現、怎麼解決、數字、commit
- **claude.ai 讀本機 repo 只用唯讀指令**，只會新增 `docs/notes-from-claude-ai.md` 與 `docs/handoffs/` 底下的檔案。遇到 `.git/index.lock` 錯誤、且確定沒有其他 git 程序在跑時，可以直接刪除後重試

## 工作規則

- **爬蟲禮貌**：每次請求至少間隔 2 秒（`REQUEST_GAP_SEC`），遵守 robots.txt。
  - **例外**：資料 API 主機 `extranet-lv.bwfbadminton.com` 的 robots.txt 是 `Disallow: /`（2026-09-30 Claude Code 發現；P0 當時只查了主站）。**Raymond 確認有授權或判斷可以抓（2026-09-30）**，所以照常使用，仍維持 2 秒間隔與低請求量。`extranet.bwf.sport` 不在此例外，不要抓tournamentsoftware 的 robots.txt 禁止程式抓取，不要用；Google 新聞 RSS 也被擋，不要用；聯合新聞網的 robots.txt 禁止 Claude / ClaudeBot / GPTBot，不要用
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
- `vue-rankingweek` 只列最近約 60 週，但**舊週次的排名表查得到**：週次清單改用 `player/ranking/publication/weeks`（`rankings --history`）。**2019-01-15 以前的排名表 API 大多固定回 500**（2017–2018 只拿到男單 97 週、男雙 14、女單 9、女雙 2、混雙 0），那段期間的排名用 `ranking_estimate`（日報標「（估算）」）。`rankings.py` 仍要每週跑
- 年度賽程 API `vue-grouped-year-tournaments` **不要加 `category[]` 篩選**（結果不可靠）；不加參數拿全年，再用每筆的 `category` 名稱判斷
- 賽事 ID 會提前分配（5901 以後是 2027–2028 年），找新賽事看日期，不看 ID
- 頒獎台積分在 2022–2024 年大多是空的，不能拿來判斷層級
- `vue-current-live` 的 `tournament_category_id` 與分類 API 的 id 是不同編號，不要混用
- 賽事頁日期只有「日 月」，年份要從賽事名稱取；跨年賽事已處理
- 冠軍積分判斷層級只適用 2018 年新制之後：4000=IC、2500=IS、1700=FS
- 頒獎台 API 的 `points` 欄在 2024 第 17 週調整後仍是舊值（Super 1000 冠軍寫 12000）或空白，不能拿來判斷 Super 1000 分級
- `day-matches` 偶爾缺場次（例：2026 亞錦賽混雙決賽不在 API），舊年度輪次有 `Semi-finals`、`3/4`、NULL 等寫法（`results.py` 已處理）
- 年終總決賽小組賽的輪次叫 R1–R3；奧運小組賽叫「Group A」等
