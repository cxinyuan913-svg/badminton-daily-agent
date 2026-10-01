# 資料來源（P0 實測，2026-09-30）

## 賽果：BWF 官網（主要來源）

Grade 1、2、3 用的是同一套元件與 API。

| 用途 | URL | 備註 |
|---|---|---|
| 賽事頁 | `https://bwfbadminton.com/tournament/{id}/x/` | slug 可以隨便填。HTML 裡有名稱（`<title>`）、GUID、日期（`<div class="live-date">`） |
| World Tour 賽事頁 | `https://bwfworldtour.bwfbadminton.com/tournament/{id}/{slug}/results/{YYYY-MM-DD}` | 與上面同一個 id |
| Grade 1 子網站 | bwfworldchampionships / bwfthomasubercups / bwfsudirmancup / bwfworldtourfinals / olympics `.bwfbadminton.com` | 同一套元件；世錦賽保留 2019 年起的歷屆結果 |
| 每日賽果 API | `https://extranet-lv.bwfbadminton.com/api/tournaments/day-matches?tournamentCode={GUID}&date=YYYY-MM-DD&order=2&court=0` | JSON，不需登入 |
| 頒獎台 API | `https://extranet-lv.bwfbadminton.com/api/vue-tournament-podium?drawCount=1&searchKey=&tmtTab=podium&tmtId={id}&tmtType=0&podiumEventCode={1-5}&isPara=false` | 前四名、獎金、積分 |
| 項目清單 API | `https://extranet-lv.bwfbadminton.com/api/vue-tournament-events?drawCount=0&searchKey=&tmtTab=podium&tmtId={id}&tmtType=0&eventName=1&isPara=false` | |
| 進行中賽事 | `https://match-centre.bwfbadminton.com/` | 首頁列出正在打的賽事與其 id |
| 進行中賽事 API | `https://extranet-lv.bwfbadminton.com/api/match-center/vue-current-live?showpara=0` | JSON：id、GUID、起訖日期。每日排程的入口（`brief/live.py`） |
| 排名週次 API | `https://extranet-lv.bwfbadminton.com/api/vue-rankingweek?rankId=2` | 只列最近約 60 週 |
| 選手排名週次 API | `https://extranet-lv.bwfbadminton.com/api/player/ranking/publication/weeks?rankingId=2&playerId={id}` | 該選手有排名的**所有**週次（周天成 34810 共 838 週，2010 起）；取長年在榜選手的聯集當歷史週次清單（`rankings --history`）。**排名表在 2019-01-15 以前大多回 500**（2026-10-01 實測，重試也一樣），2017–2018 只取得零星週次 |
| 選手年度排名明細 API | `https://extranet-lv.bwfbadminton.com/api/player/rankings/history?playerId={id}&rankingId=2&year={YYYY}` | 每位選手每年的每週排名與積分明細；尚未使用，之後可做「生涯最高第 N 名」「近一年走勢」 |
| 排名表 API | `https://extranet-lv.bwfbadminton.com/api/vue-rankingtable?rankId=2&catId={6-10}&publicationId={id}&doubles={bool}&searchKey=&pageKey={每頁筆數}&page={n}&drawCount=1` | catId 6=MS、7=WS、8=MD、9=WD、10=XD。**舊週次也查得到**（2026-09-30 更正：先前記錄「超過 60 週回傳 0 筆」有誤；實測 2019-08-06 publicationId 1497 男單第 1 桃田 103,118、第 2 周天成 86,698） |
| 選手頁 | `https://bwfbadminton.com/player/{player_id}/{slug}/` | 近期比賽含亞運等綜合賽事 |

`bwfbadminton.com` 的 robots.txt 只禁止 `/24-live-blog/` 與條款純文字頁。

**API 主機 `extranet-lv.bwfbadminton.com` 的 robots.txt 是 `Disallow: /`**（2026-09-30 發現；P0 時沒有查這台）。Raymond 確認有授權或判斷可以抓，所以照常使用，見 CLAUDE.md 工作規則。`extranet.bwf.sport`（規章 PDF 所在）同樣禁止，不抓。

### 賽事 ID

- 行事曆只列 Grade 1–2。Grade 3 要靠 ID 掃描找出來（`brief/scanner.py`）
- ID 2400 附近是 2016 年賽事，2026 年大約到 5800，會繼續往上長
- 範例：5622 = China Open 2026、5601 = World Championships 2026、5602 = World Tour Finals 2026、5766 = North Harbour International 2026、3600 = Myanmar International Series 2019

### 驗證紀錄

- China Open 2026（5622）7/22：每場有項目、輪次、每局比分、時長、種子、勝方、選手 ID
- World Championships 2026（5601）8/20：40 場，選手 ID 齊全
- North Harbour International 2026（5766，International Challenge）9/30：37 場，比賽進行中，狀態欄位有 F/O/C/I/N
- Myanmar International Series 2019（3600）：6 天共 126 場全部可寫入，男單決賽與頒獎台一致

### 團體賽（湯尤盃 2026，id 5600）

四強中國 3–0 日本：外層 `isTeamMatch=true`，比分 `3-0`；`matches` 內有 5 點，已打 3 點（F），未打 2 點（N）。單場 `matchTypeNo` 是第幾單／雙打。

### 年度賽程 API（找賽事與判斷層級的主要方法）

`https://extranet-lv.bwfbadminton.com/api/vue-grouped-year-tournaments?year=YYYY`

- **不要加 `category[]` 參數**：篩選結果不可靠（category 16 會回傳俱樂部錦標賽）
- 不加參數時回傳該年全部賽事，每筆有 `category` 名稱、GUID、起訖日期、國家、狀態（normal / cancelled / postponed / finished / unknown）
- 與頒獎台冠軍積分交叉比對 325 站，層級 **100% 一致**
- 實作：`brief/calendar.py`

2016–2026 年賽程中的 Grade 3 站數（含取消的賽事；2026-09-30 Claude Code 實跑校正，2019 的 French U17 International 屬青少年賽不計）：

| 年 | IC | IS | 合計 |
|---|---|---|---|
| 2016 | 27 | 41 | 68 |
| 2017 | 24 | 44 | 68 |
| 2018 | 22 | 37 | 59 |
| 2019 | 28 | 38 | 66 |
| 2020 | 22 | 32 | 54 |
| 2021 | 24 | 28 | 52 |
| 2022 | 35 | 28 | 63 |
| 2023 | 36 | 34 | 70 |
| 2024 | 34 | 30 | 64 |
| 2025 | 34 | 32 | 66 |
| 2026 | 41 | 34 | 75 |

依分類輸出，2016–2026 追蹤範圍內共 1,202 站（含洲際與綜合運動會）：IC 327、IS 378、Super 100–1000 與 WTF 共 346、2017 年以前舊制（Superseries、Grand Prix）65、Grade 1 共 22。
另外納入洲際個人錦標賽 52 站（亞錦賽、歐錦賽等）與綜合運動會（亞運、大英國協運動會；2018 年歸在 Other、2022 亞運團體賽歸在 Continental Team Games，用名稱補抓）。

### 賽事 ID 掃描（備援）

2026-09-30 在瀏覽器面板掃描 ID 2400–5940：3,317 個有賽事頁。
- **ID 會提前分配**：5901 以後已是 2027–2028 年賽事（例如 2028 高雄大師賽 = 5940），找新賽事要看日期，不能看 ID 大小
- 4752（巴黎奧運）、5028 的賽事頁會轉址到子網站，程式讀取失敗；年度賽程 API 可以正常取得
- 頒獎台積分在 2022–2024 年大多是空的，所以積分只能當輔助，不能當主要判斷

## 不使用的來源

| 來源 | 原因 |
|---|---|
| bwf.tournamentsoftware.com | 已改為僅限管理員登入 |
| www.tournamentsoftware.com | robots.txt 禁止 `/tournament/`、`/sport/`、`/player/` 等路徑；BWF 賽事會轉回管理員登入頁 |
| Google 新聞 RSS | robots.txt 擋下 |
| 聯合新聞網 | robots.txt 明確禁止 `Claude`、`ClaudeBot`、`GPTBot`（2026-09-30），尊重其意願不抓取 |
| 自由時報體育 `sports.ltn.com.tw` | robots.txt 對 `ClaudeBot`、`anthropic-ai`、`GPTBot` 等 `Disallow: /`（2026-10-01） |
| Yahoo 奇摩運動 `tw.sports.yahoo.com` | robots.txt 對 `ClaudeBot`、`Claude-Web`、`anthropic-ai` 等 `Disallow: /`（2026-10-01） |
| 運動視界 `www.sportsv.net` | robots.txt 對 `ClaudeBot`、`GPTBot` `Disallow: /`（2026-10-01） |
| 中華民國羽球協會最新消息 `www.ctb.org.tw/news-list.asp` | 沒有 robots.txt（404，不限制），但最新一則停在 2024 年（`news-info.asp?id=496`），暫不收（2026-10-01） |
| 截圖 + OCR | 本質上仍是自動存取；會讀錯比分、拿不到選手 ID |

## 補充與校對

- 維基百科 `2026 BWF World Tour`：整年賽程與決賽比分
- 維基百科 `2026 BWF Continental Circuit`：只有冠軍，2026 年 95 場中約 20–25 場有資料
- 各洲羽協（Badminton Europe、Badminton Asia）新聞稿

## 新聞（P1，2026-09-30 實測，`brief/news.py`）

| 來源 | 列表頁 | 取法 | 備註 |
|---|---|---|---|
| BWF 主站 | `https://bwfbadminton.com/news/` | `<a title>`；日期取自網址 `news-single/YYYY/MM/DD/` | 最即時，含亞運、青少年等 |
| BWF World Tour | `https://bwfworldtour.bwfbadminton.com/news/` | 同上；沒有 title 屬性時用圖片 alt | 只有 World Tour 賽事，9/30 時最新一則是 9/7 |
| 中央社體育 | `https://www.cna.com.tw/list/aspt.aspx` | 網址 ID 前 8 碼是日期 | robots.txt 標示 `ai-input=yes, ai-train=no`：可當 AI 輸入、不可訓練 |
| NOWnews 運動 | `https://www.nownews.com/cat/sport/` | `aria-label` + `<time datetime>` | 標籤頁 `/tag/羽球` 回 403，改用分類頁 |
| ETtoday 運動雲 | `https://sports.ettoday.net/news-list/新聞/最新新聞` | `<h3><a title>` + `<span class="date">` | robots.txt 只擋留言頁；`/sport-category/羽球` 存在但內容是各種運動，不準（2026-10-01） |
| 公視新聞 即時 | `https://news.pts.org.tw/dailynews` | `<h2 title>` + `<time datetime>` | robots.txt 擋 `/search*`、`/api/`；沒有體育分類（category 1–13 都不是），用即時列表（2026-10-01） |
| TSNA 台灣運動好事 | `https://www.tsna.com/all` | `<a title class="card-info">` + `<span class="time">` | robots.txt 擋 `/search`；列表一頁約 8 則（2026-10-01） |

- 台灣媒體列表混了所有運動，用關鍵字（羽球、湯尤盃、主要台灣選手名）篩選；選手名單請 Raymond 補充（`news.BADMINTON`）
- 列表各只有 8–30 則，大型綜合賽事期間幾小時就被擠掉：**2026-10-01 起跟 `brief.watch` 每 30 分鐘收一次**（每來源 1 個請求，已收過的網址跳過；notes 16:00）
- 符合關鍵字的台灣媒體文章抓內文存進 `foreign_article`（譯名表、暱稱、交接單 005 的場上爭議用），只存不外流；推送時只給標題＋連結＋自己寫的一句重點
- 搜尋：改用正式搜尋 API，配額與費用待查
