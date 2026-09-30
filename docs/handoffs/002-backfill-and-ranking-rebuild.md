# 交接單 002：十年比賽回補 + 前 100 名歷史排名重建

來源：claude.ai 規劃對話（2026-09-30）
前置：交接單 001 完成。**與 P1 的 daily / digest 工作不衝突，可以穿插進行**；開工前先把手上的 P1 修改提交。

## 為什麼要做
- 對戰紀錄（H2H）來自我們自己的比賽資料庫，要有十年資料才查得到完整生涯
- BWF 排名 API 只保留約 60 週。判斷「爆冷」需要比賽當下的排名，更早的要自己算
- 用 60 週官方排名驗證重建結果，可以得到具體準確率，這是面試展示重點

## 0. 先處理換行字元（小事，先做）
2026-09-30 claude.ai 檢查時，`git diff --stat` 顯示 8 個檔案整份變更（964 行增 / 964 行刪），疑似 CRLF / LF 混用。
- 加 `.gitattributes`（例如 `* text=auto eol=lf`），統一後 `git add --renormalize .`，單獨成一個 commit
- 確認之後 `git diff` 只顯示真正改動的行

## 1. 十年比賽回補（2016–2026）
- 範圍：`brief.calendar` 的追蹤範圍（CLAUDE.md 已決議表），**不含 Future Series**
- 做一支 `brief/backfill.py`：
  - 讀 tournament 表，逐站呼叫 `crawler.crawl_known`，由新到舊
  - 可中斷、可續跑：記錄每站完成狀態（例如在 `crawl_run` 或新表），重跑時跳過已完成的站
  - 跳過 cancelled / postponed
  - 請求間隔維持 2 秒。約 1,150 站 × 平均 6 天 ≈ 7,000 個請求 ≈ 4 小時，請分批或放夜間
- 完成後回報：各年、各層級的站數與比賽場數；抓取失敗的站列出來
- 抽查：隨機 5 站的決賽比分與頒獎台 API 一致

## 2. 積分規則表
- 來源：BWF 官方規章 GCR Appendix 6 – World Ranking System
  https://system.bwfbadminton.com/documents/folder_1_9/folder_1_22/folder_1_30/GCR%20Appendix%206%20-%20World%20Ranking%20System.pdf
  （2018 年舊版也有：https://fedebadchile.cl/wp-content/uploads/2019/05/3.3.3.1-World-Ranking-System-Nov2018-1.pdf）
- 做 `brief/ranking_points.py`：(日期, 層級, 打到的輪次) → 積分
- 已知要依日期切換的版本（細節以官方文件為準，查不到就問 Raymond）：
  - 2017 年以前：舊制（Superseries Premier / Superseries / Grand Prix Gold / Grand Prix）
  - 2018 年起：現行制度
  - 2024 年第 17 週起：Grade 1、World Tour Finals、Super 1000 積分調高（Super 1000 依加碼獎金分 13,500 / 12,700 兩級）；Super 750 以下不變
- 現行表（2024 W17 起）供對照，順序：冠、亞、四強、八強、16、32、64、128、256：
  - 奧運 / 世錦賽：14500 12500 10500 8200 6000 3700 1450 750 300
  - WTF：14000 12000 10000 7800 5700 3500 1400 720 280
  - S1000：13500 11500 9500 7400 5400 3300 1350 670 270
  - S750：11000 9350 7700 6050 4320 2660 1060 520 210
  - S500：9200 7800 6420 5040 3600 2220 880 430 170
  - S300：7000 5950 4900 3850 2750 1670 660 320 130
  - S100：5500 4680 3850 3030 2110 1290 510 240 100
  - IC：4000 3400 2800 2200 1520 920 360 170 70
  - IS：2500 2130 1750 1370 920 550 210 100 40
- 待確認：洲際錦標賽、綜合運動會的積分；團體賽（湯尤盃、蘇迪曼盃）不計入個人排名
- 每個版本至少一個單元測試

## 3. 每站成績
- 從 match 表推每個組合在每站、每項目「打到第幾輪」：
  冠軍 = 決賽勝方；其他人 = 輸掉的那一輪
- 注意：退賽、walkover 輸的一方也算打到該輪；輪次名稱不一致（`Final` / `F`）要統一
- 存成 `tournament_result`（pairing_id, tournament_id, event, round_reached, points, rule_version）

## 4. 每週排名重建（只算前 100 名）
- 每週一為基準日：往回 52 週的成績，比賽超過 10 站只取最好的 10 站加總
- 雙打以組合為單位
- **只輸出前 100 名**（2026-09-30 Raymond 決議）；計算時仍要算全部組合，排序後取前 100
- 2020-03-18 至 2021-02-02 排名凍結：這段期間沿用凍結前的排名，並標記 `frozen`
- 存成 `ranking_estimate`（week_date, event, pairing_id, rank, points, method）
- 可靠起點：2017 年起（需要前 52 週資料）。2016 年的週次可以算，但要標記資料不足

## 5. 驗證（最重要）
- 拿 `ranking_snapshot` 的官方排名（約 60 週）比對同週的 `ranking_estimate`
- 指標，依項目分開：
  - 前 100 名名單重疊率
  - 名次完全相同的比例
  - 名次誤差 ≤ 2 的比例
  - 積分平均絕對誤差
- 目標（暫定）：前 50 名名次誤差 ≤ 2 達 90% 以上
- 分析差異最大的 10 個案例，找出原因（缺 Future Series、洲際錦標賽積分、規則版本等）並修正或記錄
- 結果寫成 `docs/ranking-validation.md`，要能直接拿去面試講

## 6. 接上現有程式
- `rankings.rank_on`：先查官方快照，沒有再查 `ranking_estimate`，回傳時註明來源
- digest 的爆冷判斷若用到估算排名，訊息裡要標示「估算」

## 完成標準
- 2016–2026 比賽回補完成，失敗清單已處理或記錄
- 積分規則表有測試；重建排名有驗證報告
- 所有新寫入邏輯都有重跑（冪等）測試
- `docs/status.md` 更新、提交、推上 GitHub

## 做完之後
在 `docs/status.md` 寫下驗證結果的重點數字；需要 Raymond 決定的事（例如準確率不夠時要不要補抓 Future Series）用 A/B/C/D 問他。
