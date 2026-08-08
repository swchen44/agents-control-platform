# agents-control-platform (ARCP) — session 啟動須知

**開始工作前,先讀 `HANDOFF.md`** — 它有專案目標、已敲定的決策(勿再問)、
三條實作路線的真實狀態、已實測的事實、PoC 跑法、下一步清單。

**要除錯或分析(尤其在離線內網的凍結 snapshot)→ 先讀 [`docs/ai-debugging.md`](docs/ai-debugging.md)**
—— 離線工作守則、標準除錯路徑([troubleshooting](docs/troubleshooting.md) →
[observability](docs/design/observability.md) → [lessons](docs/lessons.md))、關鍵不變量。
文件總覽見 [`docs/index.md`](docs/index.md)。

## 專案一句話

讓 `claude -p` / `codex exec` 等 headless coding agent 能長時間可靠執行、可 trace、
可 control,並由 Jira 事件驅動。**OpenHands 只是候選方法之一,不是前提。**

## 與使用者協作規則(務必遵守)

1. **決策樹建模**:把計畫視為決策樹,系統性走訪每個分支與 edge case。
2. **一次只問一個問題**,每問一題就暫停等回應(勿一次拋多題)。
3. **事實靠查閱,決策才發問**:能從程式碼/環境讀到的事實一律自己查,絕不問使用者。
4. **每個問題都給選項** + 附 AI 建議答案與理由(讓使用者以審提案方式決策)。
5. **確認所有分支後才動工**,不提前寫碼或改檔。

## 目錄

- `docs/research/` — 研究報告(原始長文 + 「結論比較」策展文章;v3 最新、v2 前版)
- `examples/jira-agent-poc/` — 可跑 PoC(raw supervisor 包 claude -p / codex exec)
