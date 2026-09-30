# 交接單 001：環境建立、上 GitHub、第一次實跑

來源：claude.ai 規劃對話（2026-09-30）

## 目標
在 Raymond 的電腦上把專案跑起來、推上 GitHub，並用真實資料建立第一版資料庫。

## 要做的事
1. 讀 `CLAUDE.md`、`docs/plan.md`、`docs/data-sources.md`、`docs/status.md`
2. 建虛擬環境、`pip install -e ".[dev]"`、跑 `python -m pytest -q`，確認 20 個測試全過
3. 用 `gh` 在 cxinyuan913-svg 建立 public repo `badminton-brief` 並推上去；確認 GitHub Actions 是綠的
   - 若 `gh` 未安裝或未登入，請 Raymond 執行 `gh auth login`
4. 產生賽事清單：
   `python -m brief.calendar --from 2016 --to 2026 --db data/brief.db --csv data/tournaments.csv`
   回報各年、各層級站數，和 `docs/data-sources.md` 的表格比對
5. 實跑三站賽果，回報各寫入幾場、有沒有錯誤：
   - `python -m brief.crawler 5766 --db data/brief.db`（North Harbour International 2026，IC，進行中）
   - `python -m brief.crawler 3600 --db data/brief.db`（Myanmar International Series 2019，預期 126 場）
   - `python -m brief.crawler 5600 --db data/brief.db`（Thomas & Uber Cup 2026，團體賽）
6. 補齊排名快照：`python -m brief.rankings --db data/brief.db`（約 60 週 × 5 項，請求多，可放著跑）

## 完成標準
- GitHub 上有 repo，Actions 綠燈
- `data/brief.db` 有 2016–2026 的賽事清單、上述三站比賽、排名快照
- 實跑中發現的問題：修正並附測試，或記在 `docs/status.md`

## 注意
- 爬蟲間隔維持 2 秒，不要調低
- `data/`、`*.db` 不進版控
- 發現 API 欄位跟 fixture 不一樣時，先存一份新的真實回應到 `tests/fixtures/` 再改程式

## 做完之後
在 `docs/status.md` 最上方新增一段並提交，然後列出 P1 的工作項目與建議順序，用 A/B/C/D 讓 Raymond 選。
