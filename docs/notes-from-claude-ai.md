# 給 Claude Code 的說明（來自 claude.ai）

Raymond 每次把 CLI 的問題帶到 claude.ai 討論後，claude.ai 會在這裡新增一段，說明結論與之後怎麼配合。
**開工前先讀最上面一段**；已經照做的規則請併入 CLAUDE.md 的「與 claude.ai 的分工」，並在該段標記「已併入」。

較大的實作任務仍然用交接單（`docs/handoffs/NNN-*.md`）；這個檔案放的是協作方式、慣例與小結論。

---

## 2026-09-30 17:05 — 日報改用中文，並重新測試每日流程

**Raymond 的要求**：推到 Discord 的每日摘要（日報）一律用**繁體中文（台灣用語）**。請同步寫進 CLAUDE.md 的「已決議」表。

**中文化規則**（claude.ai 建議的預設，Raymond 可再調整）：
- **固定文字**全部中文：標題、段落名稱、統計說明、告警訊息
- **項目**：MS／WS／MD／WD／XD → 男單／女單／男雙／女雙／混雙
- **輪次**：R64／R32／R16／QF／SF／F → 64 強／32 強／16 強／八強／四強／決賽
- **狀態**：Retired → 退賽；Walkover → 不戰而勝
- **國家**：國家代碼轉中文國名，例如 TPE → 中華台北、KOR → 韓國（做成對照表，找不到就保留代碼）
- **賽事名稱**：常見賽事用中文通稱，例如 China Open → 中國公開賽、All England → 全英賽、World Tour Finals → 年終總決賽；沒有對照的保留英文
- **選手名字**：有中文名就寫「中文（英文）」，例如「周天成（CHOU Tien Chen）」；沒有就保留英文。**不要自己音譯外國選手的名字**，譯名錯了比英文更糟
  - 中文名來源：先建一個對照表（`player.name_zh` 欄位已經有），台灣、中國、港澳、日韓等選手優先；表裡沒有的就用英文
- **比分、數字**維持原樣
- **新聞**：標題可以保留原文，但每則要附一句中文重點
- **LLM「今日重點」**：提示詞明確要求用繁體中文、台灣用語

**事實檢查要跟著調整**：目前 `llm.py` 會把查不到的「英文名字」標為待確認。改成中文之後，請讓檢查同時比對中文名與英文名（透過對照表），不要因為換成中文就讓名字檢查失效。建議 LLM 輸出的名字維持「中文（英文）」格式，檢查時用括號裡的英文核對。

**重新測試每日流程**，依序：
1. 補好中文化與測試（對照表、輪次、項目、國家都要有單元測試）
2. 用真實資料跑一次 `python -m brief.daily --db data/brief.db`
3. `python -m brief.digest --db data/brief.db`（**先不要 --send**），把完整輸出貼進 `docs/status.md`，讓 Raymond 在 claude.ai 看過
4. 確認格式沒問題後，再用 `--send` 推一次到 Discord，確認新頻道收到、2000 字切分正常、中文沒有亂碼
5. 在 status.md 記錄：這次跑了哪些賽事、寫入幾場、摘要長度、推送結果、遇到的問題
6. commit、push

---

## 2026-09-30 17:00 — 進度回報方式

> **已併入** CLAUDE.md「與 claude.ai 的分工」（2026-09-30，Claude Code）

**起因**：CLI 建議 Raymond 把 GitHub 上 `docs/status.md` 的連結貼給 claude.ai。

**結論**：不需要貼連結。Raymond 說「看進度」時，claude.ai 會依序讀：
1. Raymond 電腦上的專案資料夾（`docs/status.md`、`git log`），最即時，包含還沒推上去的內容
2. 讀不到時（電腦關機或桌面版沒開），改讀 GitHub 上的 public repo

**請 CLI 配合**：
- **每次結束工作前**：更新 `docs/status.md`、commit，**並且 push**。這樣 Raymond 不在電腦前時，claude.ai 也看得到最新狀態
- **status.md 維持這次的格式**，它很好讀：完成／發現與決定／卡住／待 Raymond 決定／下一步
- **「待 Raymond 決定」每一題都要寫**：背景一句、目前暫定值或預設行為、可選的方案（A/B/C/D）。這樣 Raymond 可以直接帶到 claude.ai 討論，或直接回答你
- **數字附上出處**：寫明是哪個 commit、哪個檔案或哪個指令的輸出，claude.ai 才能核對
- **不需要**特別提供連結或整理給 claude.ai 的摘要，status.md 就是唯一的進度來源

**git 注意事項**：claude.ai 讀取本機 repo 時只用唯讀指令，不會 commit 也不會改你的檔案；只可能新增 `docs/notes-from-claude-ai.md` 與 `docs/handoffs/` 底下的檔案。2026-09-30 曾兩次在讀取時留下 `.git/index.lock`（已清除，之後改用 `--no-optional-locks` 避免）。如果遇到 index.lock 錯誤，而且確定沒有其他 git 程序在跑，可以直接刪除後重試。

**待 Raymond 決定的 5 件事**（2026-09-30 status.md）已同步到 claude.ai 的專案進度報告，之後在 claude.ai 討論出結論，會寫在這個檔案的新一段或新的交接單。
