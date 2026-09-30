# 進度紀錄

Claude Code 每次工作結束前更新這份：做了什麼、卡在哪、下一步。
Raymond 回到 claude.ai 討論時，把最新一段貼過去即可接上。

## 2026-09-30 下午（Claude Code）— 交接單 001 完成、P1 程式完成、002 開工

### 完成
**交接單 001**
- 環境：Python 3.11 venv，測試由 20 → **47 個全過**
- GitHub：public repo `cxinyuan913-svg/badminton-daily-agent` 已推上，**Actions 綠燈**（gh 以 winget 安裝，token 已含 `workflow` 權限）
- 賽事清單：2016–2026 共 **1,202 站**（IC 327、IS 378、World Tour 與 WTF 346、舊制 65、Grade 1 22、洲際 52、綜合運動會 12）
  - 修正 bug：名稱排除規則的 `para` 沒有字界，**Paraguay International Series 2023 被誤排除**；calendar 與 scanner 都已改為 `\bpara\b` 並附測試
  - `data-sources.md` 表格校正：2019 IC 為 28（文件原寫 29；多的一站是 French U17，屬青少年賽不計）；IS 總數 377 → 378
- 三站實跑，無錯誤：
  | 賽事 | 寫入 | 備註 |
  |---|---|---|
  | 5766 North Harbour International 2026（IC，進行中） | 37 場 | 9/30 全部完成；10/1 的 37 場尚未開打 |
  | 3600 Myanmar International Series 2019 | **126 場** | 與預期一致，含 1 場 Retired |
  | 5600 Thomas & Uber Cup 2026 | 289 場單場 + **62 場團體對戰** | 21 場「未打」是勝負已定而未進行的點 |
- 排名快照：API 保留的 60 週全部回補中，已完成 58 週、**約 14.5 萬筆**（2025-08-26 → 2026-09-29，每項目前 500 名）

**P1（每日收集 + 文字摘要）程式完成**
- `brief/daily.py`：賽程 → 進行中賽事交叉檢查 → 最近兩天賽果 → 新聞 → 缺少的排名週次；執行紀錄寫入 `crawl_run`
  - 追蹤範圍以 calendar 為準（live 的名稱篩選含 `team`，會把湯尤盃、蘇迪曼盃排除）
  - 資料庫沒有的賽事從開賽日整站補抓；一步失敗不擋後面
- `brief/digest.py`：從資料庫整理還沒推送過的比賽。八強以後全列；早期輪次只列**爆冷**與**台灣選手**；團體賽列國家比分；附最近兩天新聞與出處
- `brief/discord.py`：webhook 推送（2000 字切分、429 重試）；推送失敗不標記已推送；有錯誤時推告警
- `brief/news.py`：BWF 主站、BWF World Tour、中央社體育、NOWnews 運動（台灣媒體以關鍵字篩出羽球）
- `brief/llm.py`：LLM 介面（預設 claude-opus-5-5，開啟伺服器端 refusal fallback）。只根據事實摘要寫「今日重點」；輸出中**查不到的數字與英文名字自動標「⚠️待確認」**
- Discord webhook 已設定在 `.env`（不進版控），連線測試成功；目前每日與告警共用同一個頻道
- 排程腳本：`scripts/run_daily.cmd`、`scripts/register_task.ps1`（Windows 工作排程器，每天 06:00，錯過會在開機後補跑）

**交接單 002**
- 第 0 步完成：新增 `.gitattributes` 統一 LF。版本庫本來就是 LF，問題出在工作目錄的 CRLF 與 `core.autocrlf`
- `tournament` 表新增 `status` 欄位（含舊資料庫遷移），回補時用來跳過取消的賽事

### 發現與決定
- **聯合新聞網 robots.txt 明確禁止 Claude / ClaudeBot / GPTBot**，不使用（已寫入 CLAUDE.md）
- 中央社 robots.txt 標示 `ai-input=yes, ai-train=no`，本專案只當資訊來源，符合規範
- NOWnews 標籤頁回 403，改用運動分類頁 + 關鍵字
- 排名 API 只給 slug 與國家名稱，**只出現在排名裡的 2,751 位選手沒有姓名**；比賽裡的選手都有

### 卡住
- 無

### 待 Raymond 決定
1. **爆冷門檻**（目前暫定：敗方有排名，且勝方無排名或名次至少是敗方的兩倍、差距 ≥ 10 名）
2. **LLM 金鑰**：`.env` 的 `ANTHROPIC_API_KEY` 還是空的，填入後摘要才會有「今日重點」
3. **台灣選手關鍵字**：新聞篩選用的選手名單（`brief/news.py` 的 `BADMINTON`）需要補充
4. **告警頻道**：要不要另開一個 Discord 頻道給錯誤告警
5. 排程放在這台電腦（需開機），或 P4 再搬到雲端

### 下一步
1. 排名跑完 → 用真實資料實跑 `brief.daily --send`，推出第一份每日摘要
2. 註冊每日 06:00 排程，開始累積 P1 關卡「連續 7 天沒有漏抓」
3. 交接單 002：十年比賽回補（約 7,000 請求、4 小時，背景執行）→ 積分規則表 → 排名重建與驗證

## 2026-09-30（claude.ai）

- 完成：P0 資料來源實測；資料層原型（賽果、排名、進行中賽事、團體賽、年度賽程、ID 掃描），20 個測試
- 完成：十年賽事清單的取得方式確定為年度賽程 API（與冠軍積分交叉驗證 325 站全部一致），2016–2026 年 Grade 3 共約 700 站
- 更正：North Harbour International 2026 是 International Challenge（先前誤記為 International Series）
- 決議：洲際個人錦標賽與綜合運動會（亞運等）納入追蹤範圍
- 待決定：無
- 下一步：交接給 Claude Code → 推上 GitHub → 本機產生賽事清單並實跑爬蟲 → 開始 P1
