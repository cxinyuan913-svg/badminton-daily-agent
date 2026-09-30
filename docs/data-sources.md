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
| 選手頁 | `https://bwfbadminton.com/player/{player_id}/{slug}/` | 近期比賽含亞運等綜合賽事 |

`bwfbadminton.com` 的 robots.txt 只禁止 `/24-live-blog/` 與條款純文字頁。

### 賽事 ID

- 行事曆只列 Grade 1–2。Grade 3 要靠 ID 掃描找出來（`brief/scanner.py`）
- ID 2400 附近是 2016 年賽事，2026 年大約到 5800，會繼續往上長
- 範例：5622 = China Open 2026、5601 = World Championships 2026、5602 = World Tour Finals 2026、5766 = North Harbour International 2026、3600 = Myanmar International Series 2019

### 驗證紀錄

- China Open 2026（5622）7/22：每場有項目、輪次、每局比分、時長、種子、勝方、選手 ID
- World Championships 2026（5601）8/20：40 場，選手 ID 齊全
- North Harbour International 2026（5766）9/30：37 場，比賽進行中，狀態欄位有 F/O/C/I/N
- Myanmar International Series 2019（3600）：6 天共 126 場全部可寫入，男單決賽與頒獎台一致

## 不使用的來源

| 來源 | 原因 |
|---|---|
| bwf.tournamentsoftware.com | 已改為僅限管理員登入 |
| www.tournamentsoftware.com | robots.txt 禁止 `/tournament/`、`/sport/`、`/player/` 等路徑；BWF 賽事會轉回管理員登入頁 |
| Google 新聞 RSS | robots.txt 擋下 |
| 截圖 + OCR | 本質上仍是自動存取；會讀錯比分、拿不到選手 ID |

## 補充與校對

- 維基百科 `2026 BWF World Tour`：整年賽程與決賽比分
- 維基百科 `2026 BWF Continental Circuit`：只有冠軍，2026 年 95 場中約 20–25 場有資料
- 各洲羽協（Badminton Europe、Badminton Asia）新聞稿

## 新聞（P1 待辦）

- BWF 官方新聞列表 `https://bwfworldtour.bwfbadminton.com/news/`：有標題、日期、固定網址 `news-single/YYYY/MM/DD/slug/`；RSS feed 是空的，改讀列表頁
- 台灣媒體：網頁搜尋找得到聯合新聞網、NOWnews 等羽球報導；聯合新聞網體育 RSS 實測為空，要改讀列表頁
- 搜尋：改用正式搜尋 API，配額與費用待查
