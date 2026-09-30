# 進度紀錄

Claude Code 每次工作結束前更新這份：做了什麼、卡在哪、下一步。
Raymond 回到 claude.ai 討論時，把最新一段貼過去即可接上。

## 2026-09-30 下午（Claude Code）— 交接單 001 完成、P1 程式完成並上線、002 開工

出處代號：〔DB〕= 對 `data/brief.db` 的 SQL 查詢；〔log〕= 指令輸出；commit 以短 hash 標示。

### 完成
**交接單 001**
- 環境：Python 3.11 venv；測試 20 → **47 個全過**〔`python -m pytest -q`，commit 71afaf8〕
- GitHub：public repo `cxinyuan913-svg/badminton-daily-agent`，**Actions 綠燈**〔`gh run list`，71afaf8 success〕。gh 以 winget 安裝，token 含 `workflow` 權限
- 賽事清單：2016–2026 共 **1,202 站**〔DB：`SELECT COUNT(*) FROM tournament`；`python -m brief.calendar --from 2016 --to 2026`〕
  - IC 327、IS 378、World Tour 與 WTF 346、舊制 65、Grade 1 22、洲際 52、綜合運動會 12〔`data/tournaments.csv` 依 level 統計〕
  - 修正：名稱排除規則的 `para` 沒有字界，**Paraguay International Series 2023（id 4947）被誤排除**〔fdf32f4 calendar、bf1f91e scanner，皆附測試〕
  - `data-sources.md` 表格校正：2019 IC 為 28（原寫 29，多的一站是 3497 French U17，屬青少年賽）；IS 總數 377 → 378〔fdf32f4〕
- 三站實跑，無錯誤〔`python -m brief.crawler <id> --db data/brief.db` 的 log；DB：`match`、`team_tie` 依 tournament_id 計數〕
  | 賽事 | 寫入 | 備註 |
  |---|---|---|
  | 5766 North Harbour International 2026（IC，進行中） | 37 場 | 9/30 全部完成 |
  | 3600 Myanmar International Series 2019 | **126 場** | 與預期一致，含 1 場 Retired |
  | 5600 Thomas & Uber Cup 2026 | 289 場單場 + **62 場團體對戰** | 21 場「未打」是勝負已定而未進行的點 |
- 排名快照：API 保留的 **60 週全部存下**，共 **149,843 筆**，2025-08-12 → 2026-09-29，每項目約前 500 名〔DB：`SELECT COUNT(*), COUNT(DISTINCT week_date) FROM ranking_snapshot`；`python -m brief.rankings` 約 60 分鐘〕

**P1（每日收集 + 文字摘要）程式完成並上線**
- `brief/daily.py`〔a133530〕：賽程 → 進行中賽事交叉檢查 → 最近兩天賽果 → 新聞 → 缺少的排名週次；紀錄寫入 `crawl_run`
  - 追蹤範圍以 calendar 為準（live 的名稱篩選含 `team`，會把湯尤盃、蘇迪曼盃排除）；資料庫沒有的賽事從開賽日整站補抓
- `brief/digest.py`、`brief/discord.py`〔1d95b8c〕：八強以後全列，早期輪次只列爆冷與台灣選手，團體賽列國家比分；推送失敗不標記已推送，有錯誤時推告警
- `brief/news.py`〔eaf6177〕：BWF 主站、BWF World Tour、中央社體育、NOWnews 運動
- `brief/llm.py`〔a673c81〕：LLM 介面（claude-opus-5-5，開啟伺服器端 refusal fallback）；只根據事實摘要寫「今日重點」，查不到的數字與英文名字自動標「⚠️待確認」
- **第一次真實執行**：`python -m brief.daily --db data/brief.db --send`，4 站、**195 場**、0 錯誤，摘要已推到 Discord〔DB：`crawl_run` run_id=1；`digest_item` 195 筆〕
  - 5874 亞運個人賽 156 場（整站補抓）、5766 North Harbour 37 場、5768 Dutch Open 2 場、5753 Guatemala IC 0 場（當天剛開打）
- **每日排程已註冊**：Windows 工作排程器 `badminton-daily-agent`，每天 06:00，錯過會在開機後補跑；第一次是 2026-10-01 06:00〔`Get-ScheduledTask`；腳本 511c1cc〕。輸出寫到 `data/logs/daily.log`
- Discord webhook 設定在 `.env`（不進版控），每日與告警目前共用同一頻道

**交接單 002**
- 第 0 步：新增 `.gitattributes` 統一 LF〔4149e18〕。版本庫本來就是 LF，問題出在工作目錄的 CRLF 與 `core.autocrlf=true`
- `tournament` 表新增 `status` 欄位與舊資料庫遷移，回補時用來跳過取消的賽事〔e9bf238〕

**協作方式**
- 依 `docs/notes-from-claude-ai.md`（2026-09-30 17:00）調整：結束前必 push、「待決定」附選項、數字附出處；已併入 CLAUDE.md

### 發現與決定
- **聯合新聞網 robots.txt 明確禁止 Claude / ClaudeBot / GPTBot**，不使用（已寫入 CLAUDE.md 工作規則）
- 中央社 robots.txt 標示 `ai-input=yes, ai-train=no`；本專案只當資訊來源、不訓練，符合規範
- NOWnews 標籤頁 `/tag/羽球` 回 403，不硬闖，改用運動分類頁 + 關鍵字
- 排名 API 只給 slug 與國家名稱：**只出現在排名裡的 2,751 位選手沒有姓名**；比賽裡的選手都有〔DB：`player` 中 `name_display IS NULL`〕
- **2025-08-19 那週混雙官方只公布 85 名**（其他週約 500 名）：重抓確認 API `total=85`，不是抓取錯誤〔`vue-rankingtable` publicationId=3896 catId=10〕

### 卡住
- 無

### 待 Raymond 決定
1. **爆冷門檻**
   - 背景：摘要與之後的題材評分都靠它挑「爆冷」
   - 暫定（`brief/digest.py` 的 `is_upset`）：敗方有排名，且勝方無排名、或勝方名次 ≥ 敗方名次 + max(10, 敗方名次)，也就是至少兩倍
   - A. 維持暫定　B. 固定差距：勝方比敗方低 ≥ 20 名　C. 依層級分開（World Tour 用前 32 名被擊敗、Grade 3 用兩倍規則）　D. 由 Raymond 另訂
2. **LLM 金鑰**
   - 背景：`.env` 的 `ANTHROPIC_API_KEY` 還是空的，所以摘要沒有「今日重點」段落；其餘功能不受影響
   - 預設：沒有金鑰就略過
   - A. Raymond 自行填入 `.env`　B. 先不用 LLM，P3 再開　C. 改用其他模型（介面已包好）
3. **新聞篩選用的台灣選手名單**
   - 背景：中央社、NOWnews 的標題常只寫人名，不寫「羽球」
   - 暫定（`brief/news.py` 的 `BADMINTON`）：戴資穎、周天成、王齊麟、李洋、林俊易、王子維、李佳馨
   - A. Raymond 提供完整名單　B. 改從資料庫的台灣選手自動產生（需要先補中文名 `name_zh`）　C. 維持暫定
4. **告警頻道**
   - 背景：目前錯誤告警和每日摘要推到同一頻道
   - A. 維持同一頻道　B. 另開告警頻道，給我新的 webhook　C. 告警改用其他方式（例如 email）
5. **排程位置**
   - 背景：排程目前在這台電腦，關機期間不會跑，開機後才補跑一次
   - A. 維持本機，P4 再搬雲端　B. 現在就搬到雲端小主機　C. 本機 + GitHub Actions 每日檢查有沒有漏跑

### 下一步
1. 明天 06:00 看第一次自動執行的結果，開始累積 P1 關卡「連續 7 天沒有漏抓」
2. 交接單 002：十年比賽回補 `brief/backfill.py`（約 7,000 請求、4 小時，背景執行）
3. 積分規則表 → 每站成績 → 排名重建 → 用 60 週官方排名驗證

## 2026-09-30（claude.ai）

- 完成：P0 資料來源實測；資料層原型（賽果、排名、進行中賽事、團體賽、年度賽程、ID 掃描），20 個測試
- 完成：十年賽事清單的取得方式確定為年度賽程 API（與冠軍積分交叉驗證 325 站全部一致），2016–2026 年 Grade 3 共約 700 站
- 更正：North Harbour International 2026 是 International Challenge（先前誤記為 International Series）
- 決議：洲際個人錦標賽與綜合運動會（亞運等）納入追蹤範圍
- 待決定：無
- 下一步：交接給 Claude Code → 推上 GitHub → 本機產生賽事清單並實跑爬蟲 → 開始 P1
