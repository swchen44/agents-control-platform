# ARCP 文件

ARCP(Agent Runtime / Control Plane)讓 `claude -p` / `codex exec` 這類 headless
coding agent 由 **Jira 事件驅動**、長時間可靠執行、可觀測(trace)、可控制(control)。

## 從這裡開始

| 你是… | 讀這份 |
|---|---|
| **想跑起來的使用者** | [使用者手冊](user-guide.md) — 安裝、設定、跑 poller、看 dashboard、人機互動 |
| **想改程式的開發者** | [開發者手冊](developer-guide.md) — 架構、模組、測試、加 backend、CI/CD |
| **想看全貌** | [專案檔案介紹](project-overview.md) — 目錄地圖、每個檔案在幹嘛 |
| **想懂「為什麼這樣設計」** | [需求與理由](requirements.md) — 每個能力的 What / Why / 現狀 |
| **想懂重大決策** | [決策記錄](decisions.md) — 關鍵取捨與理由(ADR-lite) |
| **有疑問** | [FAQ](faq.md) |

## 深入設計

`docs/design/` 有各子系統的機制細節:
[生命週期](design/lifecycle.md) ·
[模組架構](design/architecture.md) ·
[互動服務(HIL 人機介面)](design/interaction.md) ·
[執行隔離](design/isolation.md) ·
[冪等](design/idempotency.md) ·
[熱重載](design/hotreload.md) ·
[transcript](design/transcript.md)

## 一句話世界觀

**Jira = 對外的工作日誌 / System of Record**;Agent 以「員工」身分接單 → 做事(後台)
→ 更新進度 → 回報成果讓人評分關單。真正的工作與細節在後台(workspace = 工作台;
dashboard/transcript = 完整飛行記錄器),Jira 只承載策展後的摘要/決策/結果/連結。
