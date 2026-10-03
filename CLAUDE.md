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
| LLM | 雲端 API，`brief/llm.py` 包一層介面。**依用途選模型**：例行（今日重點、新聞重點、暱稱擷取）`claude-sonnet-5-5`，重要（賽前分析、影片草稿、驗證差異分析）`claude-opus-5-5`；可用 `.env` 的 `LLM_MODEL_ROUTINE`／`LLM_MODEL_HEAVY` 覆寫。每次呼叫記錄用途、模型、token、估計費用到 `llm_call`（2026-09-30）。**寫作一律 Opus（effort low）**：影片故事、粉專所有類型（low 不過再試 medium）、今日重點；**編輯檢查 Sonnet low**；暱稱擷取等非寫作照舊 routine（2026-10-01 notes 23:15） |
| 推送 | Discord webhook，推到既有伺服器的指定頻道 |
| 發布 | 系統只產草稿，Raymond 審稿、錄製、發布；**不做自動發文** |
| 影片 | 不用轉播畫面；本人入鏡 + 數據圖卡。新聞只當資訊來源，草稿要改寫並附出處 |
| 爆冷 | 敗方必須是本站**種子**（種子序號依當次報名排出，不等於世界排名）。以比賽當週世界排名判斷：種子排名在前 100、輸給 100 名以外或無排名 = **大爆冷**；種子排名在前 50、輸給 50 名以外 = **爆冷**。不戰而勝不算（`digest.upset_level`，2026-09-30） |
| 今日重點評語 | 「逆轉」「三局大戰」這類可由比分驗證的詞可以用；**「爆冷／冷門」只能用在 `upset_level` 判定的場次**，事實檢查會擋下違反的輸出（`llm.unlicensed_upsets`）（2026-09-30 22:45）。句子沒寫名字時：寫兩個排名且對得上同一場、或寫「項目＋決賽／冠軍」且該項目決賽有判定，也放行（2026-10-01 13:35 B） |
| 日報語言 | 推到 Discord 的日報一律繁體中文（台灣用語）：項目、輪次、狀態、國家、常見賽事名稱轉中文（`brief/zh.py`）；比分與數字不變。選手名字**不音譯**，只用 `brief/player_zh.csv` 人工確認過的中文名，格式「中文（英文）」，其餘保留英文（2026-09-30） |
| 日報推送範圍 | **推送**：Grade 1（奧運、世錦賽、湯尤盃、蘇迪曼盃）、年終總決賽、Super 1000／750／500／300／100、洲際個人錦標賽（亞錦賽等）、綜合運動會（亞運等）。**IC／IS 照常抓、照常存，不推**，只推明確規則的例外（`brief/grade3.py`），每則一行理由＋該選手當天最後一場（22:45）：① 選手曾在 Super 750 以上或 Grade 1 打進八強 ② 官方排名曾進前 30 名 ③ 中華台北選手拿到**冠軍、亞軍、季軍**（四強敗者），一站一行，賽事進行中先推「確定至少季軍／亞軍」、決賽後隔天更新。日報結尾列「今天另有 IC／IS 共 N 場，已存入資料庫」。例外不交給 LLM 判斷（2026-09-30） |
| 空檔週 | 某一 BWF 週（週一到週日）**完全沒有推送層級賽事**（賽期與這週重疊就算有）時，這週的 IC／IS 升格，由 `brief.watch` 每站當地當天打完就發：標題標層級（「…｜IC｜第 3 天 16 強」），八強以前只列中華台北與爆冷場次、八強起全列，最後一行「另有 N 場未列」；晨報不再重複列。Future Series 不升格（`grade3.quiet_week`，2026-09-30 23:15）。**IC／IS 的四強日、決賽日不論是不是空檔週都由 watch 逐站發**，晨報不重複列；四強以後的場次也是故事與粉專素材（2026-10-01 21:05）。已取消的賽事（狀態 cancelled／postponed，或名稱帶 (Cancelled)）不算 |
| 世界排名來源 | **官方歷史排名**：`player/ranking/publication/weeks` 取週次、`vue-rankingtable` 取每週前 100 名，2017-01 起存進 `ranking_snapshot`；日報的排名一律用官方資料。自行重建（`ranking_estimate`、`validate`、`docs/ranking-validation.md`）**收尾不再加功能**，保留作驗證與方法展示。規章 PDF 在 `docs/regulations/` 備查（不進版控）（2026-09-30 23:10） |
| 賽果推送時機 | **每站在當地當天最後一場打完後單獨發一則**（`brief/watch.py`，工作排程器每 30 分鐘）：標題（第 N 天、最深輪次、台灣時間幾點打完）、今日重點（LLM routine＋事實檢查）、當天全部賽果、明日看點（決賽日改列本站冠軍）。當地隔天 03:00 仍沒打完就保險發送並列「未完成：N 場」。明日賽程未公布時，之後另發一則看點（只發一次、最晚第一場開打前）。06:00 改為**晨報**：最近 24 小時新聞＋IC／IS 精選＋週一暱稱週報＋漏發提醒，全都沒有就不發（2026-09-30） |
| 明日看點 | 選場規則（`brief/preview.py`，理由由規則產生，**不用 LLM**）：① 中華台北選手 ② 雙方都在世界前 10 ③ 過去 12 個月交手且上次是決賽／四強 ④ 交手懸殊但最近弱勢方贏 ⑤ 追蹤中的台灣選手對上今年贏過他的人。最多 8 場，台灣選手優先，依台灣時間排序；時間寫「14:30（台灣）」「約 HH:MM 後，接第 N 場」「不早於…」「時間未定」。交手戰績只用資料庫（2026-09-30） |
| 暱稱 | 由系統從新聞自動收集（`brief/nickname.py`），Raymond 不手填。規則（XY配、小X、X神）＋ LLM（必須附原文證據）找候選；同篇出現全名且 ≥ 2 個來源或累積 ≥ 3 次才自動採用。採用的暱稱只用於新聞篩選，**日報提到選手不用暱稱**。每週一日報最後列本週新增與待確認的暱稱，Raymond 回覆確認或否決（2026-09-30） |
| 退休選手 | 名單「追蹤」設 N，比賽資料保留、生涯照常可查。新聞關鍵字是否保留，看退休後的新聞是否仍以羽球為主：**戴資穎保留**；**李洋移除**（現任運動部部長，新聞多為政策）；「麟洋配」暱稱保留（2026-09-30） |
| 台灣選手名單 | `config/players_zh.csv`（Raymond 維護中文名與是否追蹤；claude.ai 起草為 `players_zh_draft.csv`）。程式優先讀這份，不存在時用 `brief/player_zh.csv`。**中文名一律照表，不自行翻譯或音譯**（2026-09-30）。歷史選手：`brief/ctba.py` 用中華羽協甲組名單（`config/ctba/*.pdf`，不進版控）拼音比對，**距離 0 且唯一才自動採用**（追蹤 N），其餘列 `config/ctba/review.csv` 請 Raymond 確認（10-02） |
| 外國選手譯名 | `brief/foreign_names.py`：**只用台灣媒體原文**（中央社、NOWnews）「中文（English）」當證據；華裔選手可用原文漢字＋拼音對回 BWF 名字（姓氏、分隔字、國家規則）。2 個不同來源才 confirmed，衝突以中央社為準；Raymond 填的鎖定不覆蓋。**不用 LLM 或拼音自己造譯名**；沒有譯名就用英文（交接單 003，2026-10-01）。維持 ≥ 2 個台灣媒體來源才 confirmed、不另外補名單（2026-10-01 21:00b Raymond 選 B）。**10-02 18:55 當時的 35 位 candidate 全部改 Raymond 確認**；之後新出現的 candidate 照原規則，週一譯名週報列出 |
| 影片腳本 | **10-03 Raymond 改版**：每天 **18:00 固定產 1 支**（`script.run_daily`：昨天所有賽事分數最高的故事；週二沒有比賽故事才寫排名更新；沒比賽不產），不再每站打完就寫；**全部推 Discord**（`.env` SCRIPT_*=1）；**只有事實錯誤擋下**（`story.is_hard`：查不到的數字、爆冷／逆轉／首冠沒根據、引用對不上、格式壞），字數、結構、組數、伏筆、關鍵對手等不過**照樣產出、列在最前面**（不合格的伏筆不登記）。以下為原設計：**故事引擎**（交接單 005，取代 10:15／10:20 的「每種風格各一份」，2026-10-01）：`brief/story.py`＋`brief/storylines.py` 的故事候選（宿敵 rivalry、完全宰制 domination、復仇 revenge、卡關輪次 stuck_round、紀錄 record、退賽 retired）。每個比賽日（含決賽日）分數最高的 1 支（**10-03 Raymond：一天最多一支**，原本可再加 1 支台灣故事；台灣故事靠 +3 加分競爭；粉專台灣戰報仍會找台灣故事當主角）；門檻 `STORY_MIN`=9、台灣故事 +3，低於門檻不產。結構：開場 0–4s → 故事 → 冷知識 → 賽果背景（可省略）→ 互動，30–60 秒。**10-03 起改用提示詞 v2**（`docs/video/script-prompt-v2.md`，每次讀檔）：鉤子 → 反差鋪陳 → 高潮還原 → 價值段（冷知識／歷史紀錄優先，沒有才寫金句，`value_kind`）→ 伏筆 → 留言問題；口播 200–300 字，超過 300 退回、短於 200 要寫 `short_reason`（notes 10-02 18:55 定 260；10-03 Raymond 因一直產生失敗改 300）。週二排名更新 1 支。模型 opus、**effort low**（15:20 實驗），max_tokens 4000。**只用官方排名**；事實檢查不過重試一次，再不過只丟那一支並告警。`.env` 開關 `SCRIPT_FINAL`／`SCRIPT_DAILY`／`SCRIPT_WEEKLY`：**目前都是 `dry`**（只寫檔 `data/scripts/`），`1` 推 `DISCORD_WEBHOOK_SCRIPTS` |
| 獎牌用詞 | **所有賽事都可以寫金牌／銀牌／銅牌**：冠軍＝金、亞軍＝銀、四強＝銅；**不用「季軍」「第三名」**（事實檢查會擋）。取代 10-01 早上「只有頒獎牌的賽事才寫」的規則（2026-10-01 notes 23:15，Raymond） |
| 寫作規則 | 所有腳本、粉專貼文、今日重點都適用（notes 15:40）：① 名次用語見「獎牌用詞」（23:15 起所有賽事可寫金銀銅、不用季軍） ② 外國選手有台灣媒體譯名（confirmed）用中文、否則英文；**同一人所有輸出只有一種寫法**；有中文名卻只寫英文也擋 ③ 素材不夠就短（30–45 秒可以），**不准換角度重講同一個比分** ④ **一個故事當主軸**，賽果當背景 ⑤ AI 可以推測資料庫以外的背景，但每句標「⚠️推測」、最後附待查清單；推測句不進事實檢查、不能寫成確定語氣 ⑥ 不准自己算出事實清單沒有的新數字（2026-10-01） |
| 審稿準則與編輯檢查 | **`docs/video/review-guidelines.md`**（R1、R2…，Raymond 的回饋由 claude.ai 持續累積）：故事與粉專的提示詞**每次執行讀最新版全文**。故事腳本通過事實檢查後，**Sonnet low 編輯**逐條打分（task `script_editor`）；不通過 → 帶意見重寫（最多 2 次，也要過事實檢查），仍不過就在腳本最後列「編輯意見」照樣產出。賽果背景只放故事主角同一批人或同一場（R3）；台灣戰報只在粉專（2026-10-01 notes 20:35）。粉專也接編輯檢查；R5–R7 有固定檢查：結尾簽名「🏸 Raymond 的羽球筆記」、不用 Markdown／花體字、距離最新比賽超過 1 天開頭要【…故事】、**數字全篇 ≤ 5、每段 ≤ 1**（一串局分、一組勝負算 1 個，年份不算）。R8：故事與粉專素材附 `career`（生涯最高官方排名、目前排名與落差、Super 500 以上冠亞軍）（21:00、21:15）。R9 不寫廢話：「對手後來奪冠」只在八強或更早輸球時產生（資料層擋）。R10：只講 1–2 組主角，文中選手／組合 > 4 組就退回（固定檢查）；台灣戰報只留當天最有故事的一場，其他一句帶過。R11：素材固定三區【歷史】（必備）【新聞】（主角近 30 天新聞內文，有就要用）【冷知識】（只用 `[x]` 條目且相關，例：衛冕 → 第 6 條）（23:10、23:15）。**編輯改 Opus low**；R7 固定檢查只看全篇 ≤ 5 個數字，「每段 1 個」交給編輯參考（10-02 05:55） |
| LLM 預算 | 台灣時間一天累計超過 `LLM_DAILY_BUDGET_USD`（1.0）→ 腳本與粉專貼文一律跳過、告警一次；日報與今日重點照跑。每次檢查結果記入 `script_check`（2026-10-01 notes 15:20） |
| 粉專貼文 | **10-03 Raymond**：改每天 **18:00**；寫手**直接 Opus medium**；**週一＝上週台灣選手全部戰報**（賽事依層級由高到低，`fbpost.LEVEL_ORDER`）、**週二＝排名變動報告**（台灣所有前 100、各項目前 10、前 50 升降各 3），兩者字數不限、不套 R7／R10（`LONG_KINDS`）；編輯維持 Opus low。以下為原設計：交接單 006（2026-10-01）：`brief/fbpost.py` 每天台灣時間 07:00 產 **1 則**草稿；依序：台灣戰報（昨天有台灣選手）→ 故事貼文 → 冷知識（週二排名、歷史上的今天、宿敵／宰制輪流；規則類只用 `config/trivia_rules.md` 勾成 `[x]` 的條目（21:00b：勾 1、4–12，共 10 條））。正文 150–400 字（台灣戰報 500 字，20:20 A）、表情 ≤ 5、hashtag 3–5（含 #羽球、不能有空格）。**不串 Meta API、不自動發文**；`FBPOST=dry`（預設，只寫 `data/posts/`），`1` 才推 `DISCORD_WEBHOOK_FBPAGE`（21:00：10-02 dry-run 通過才改 1；**沒通過，維持 dry**）。預算擋下時粉專自己發告警（不跟腳本共用一次）。沒有中文名的台灣選手列在「給你的備註」。**Opus low，不過再試 medium**（23:15）。**更新（10-02）**：字數故事貼文、台灣戰報 500–1,800、冷知識 300–1,200（notes 21:10）；`FBPOST=1`，**已在主機推送**；同一天只推一次（`fbpost_sent`）；範例載入時把未 confirmed 的外國選手中文名換回英文；指定多故事主題時每個都要講到、不適用 R10 組數上限；備註列「教練觀點」建議位置（不代寫） |
| 新聞來源 | 中央社、NOWnews、ETtoday、公視、TSNA（robots 允許）＋BWF 兩站；**自由時報、Yahoo 奇摩運動、運動視界、聯合新聞網禁止 AI 爬蟲，不用**；羽協最新消息停在 2024 不收。跟 `brief.watch` 每 30 分鐘收一次，符合關鍵字的台灣文章抓內文（只存不外流）（2026-10-01 notes 16:00）。**篩選**：標題有羽球，或全文羽球／羽毛球 ≥ 2 次，或選手名 ≥ 2 次（**名字至少 3 個字才算**，10-03 1A：「馬丁」誤中「馬丁利」）。**內文只取本文容器**（`foreign_names.BODY_START`：中央社、ETtoday 取容器內 `<p>`，nownews、公視取容器全文；找不到容器退回整頁 `<p>`，10-03 2A） |
| 雲端主機 | **暫停，維持本地**（2026-10-02 06:35 Raymond：電腦 24 小時開著，雲端化目前沒有實際好處）。所有排程在本機 Windows（watch 每 30 分、daily 06:00、fbpost 07:00、backup 04:00）。Vultr 主機（`ssh bda-vultr`，本專案專用金鑰）上的程式與 systemd 單元**保留、全部 disabled**，`.env` 與測試資料庫留著、不再同步；主機只當**異地備份櫃**（每週一本機推一份 gzip 備份到 `/root/badminton-backups/`，保留 8 份）。交接單 004 第 4 步「切換」取消。10-02 06:02–06:41 曾短暫切到主機（推了 10-02 那則粉專），已切回本機 |
| 大賽賽前看點 | **10-03 Raymond 新增**（`brief/pretournament.py`）：明天開賽的 Super 750 以上（S750、S1000、年終總決賽）與 Grade 1 個人賽，**開賽前一天 18:00** 產 1 篇粉專（字數不限）＋1 支影片，推 Discord：開賽日賽程 API 的種子、首日台灣選手對戰＋對手最近 5 站、首日焦點（前 10 對決、宿敵重演）。賽程未公布就不產、告警一次；每站一次（`pretournament_sent`）；不受每日預算擋。下一站：10/13 丹麥公開賽（10/12 18:00） |
| 關鍵對手脈絡 | 故事主角這站碰到賽前**世界前 3** 或 `config/popular_players.csv`（Raymond 維護）裡的對手時，事實清單加 `opponent_context`（`brief/storylines.py`）：近 12 個月交手、對手最近一次冠軍與之後各站名次（不含團體賽）、這場是否為對手 2025 年以來最懸殊敗場（局分差總和）、對手近 2 年敗場依選手／國家（≥ 3 次才列）。每條附日期，全部可由查證員查資料庫核對（notes 10-02 13:35）。有【關鍵對手】脈絡時，正文至少一句要引用其中一條，否則退回重寫（`cite.opponent_check`，notes 10-02 18:55） |
| 伏筆（R16） | 每篇故事型 FB 貼文與影片腳本埋一個伏筆（`brief/hooks.py`，`story_hook` 表）：事實清單另給最多 3 條支線素材（目前只有「關鍵對手近 2 年的剋星」一種），寫手挑一條只寫懸念、輸出 `hook`；程式檢查一篇一個、句子在正文、答案關鍵字（名字、姓氏）不出現；編輯看 R16。答案只寫在給 Raymond 的備註。沒有新賽果的日子，粉專冷知識（週二排名之後）優先挑最舊的 open 伏筆寫「填坑專題」，開頭接回原文，寫完 filled。超過 21 天或主角之後有新比賽 → dropped。週一晨報列 open 與作廢的伏筆（notes 10-02 13:45） |
| 外國譯名（手動） | KIM Won Ho＝金元昊、SEO Seung Jae＝徐承宰、Leo Rolly CARNANDO＝卡爾南多、Daniel MARTHIN＝馬丁：`foreign_name` source=raymond、confirmed，程式不會蓋掉；其餘仍是 ≥ 2 來源或 Raymond 確認才用中文（notes 10-02 13:45） |
| 排程執行方式 | **背景執行、不跳視窗**（notes 10-02 17:05）：7 個 badminton-* 排程（含 backfill-bwf、backfill-tsna）都由 `scripts/register_task.ps1` 註冊，保留 WakeToRun、StartWhenAvailable。首選 S4U（「不論使用者是否登入都執行」，不存密碼，**需系統管理員**：`powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1`）；備案 `-Headless`（Action 改 `conhost.exe --headless cmd.exe /c …`，不需系統管理員）。10-02 17:05 先套備案，watch 手動觸發 exit 0。**10-03 Raymond 已用系統管理員跑完，7 個排程都是 S4U**，watch 手動觸發 exit 0 |

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
- **三方合作**（2026-10-02 notes 06:55）：
  | 角色 | 負責 | 會改的檔案 |
  |---|---|---|
  | claude.ai | 和 Raymond 討論、定案；寫 notes 與交接單，每項標 ☁️ 或 💻 | 只有 `docs/notes-from-claude-ai.md`、`docs/handoffs/`、`docs/video/`、`config/`（直接寫本機檔，不 commit） |
  | 本機 CLI 💻（這裡） | 需要資料庫、網路、排程、真實試跑的工作；**唯一可以直接 commit 到 main 的角色**；合併後同步 | 全部 |
  | 雲端工作階段 ☁️ | 只用假資料就能完成的程式修改；**一律在新分支、開 PR**，不直接推 main | 程式與測試；不改 `docs/status.md`、`docs/notes-from-claude-ai.md`、`CLAUDE.md`、`config/` |
  1. 雲端的完成報告寫在 PR 說明；本機 CLI 合併後把重點抄進 `docs/status.md`（status.md 只有本機 CLI 在改）
  2. 同一個 notes 項目只標 ☁️ 或 💻 其中一個；沒標的預設 💻
  3. 開工前一律先 `git pull`。雲端 PR 開著的期間，本機 CLI 不改那個 PR 動到的檔案；真的要改，等合併後再改
  4. claude.ai 寫在本機的 notes／config 變更，由本機 CLI 下次 commit 一起推上 GitHub，雲端才看得到：**派雲端任務前，本機 CLI 要先 commit、push**
  5. PR 合併由 Raymond 在 GitHub 按；或 Raymond 說「合併」時由本機 CLI 用 `gh pr merge` 合併
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
