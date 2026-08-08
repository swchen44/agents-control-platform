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
| **想看研究與對照** | [Research](research/README.md) — 研究/實驗的結論比較文章 + 原始長文 |
| **想看開發歷程** | [History](history/README.md) — 各波實作計畫 + 真 Jira 實測(過程稿) |
| **要除錯/分析** | [除錯 runbook](troubleshooting.md) — 症狀 → 診斷 → 處置 |
| **在離線內網(AI/人)** | [離線除錯導引](ai-debugging.md) — 凍結 snapshot 的工作守則與起點 |
| **有疑問** | [FAQ](faq.md) |

## 除錯 / 可觀測

離線內網除錯的地基(交付為凍結 snapshot,只能靠 repo 內文件 + runtime 證據):

- **[離線除錯導引](ai-debugging.md)** — 給 AI/人的起點:離線守則、標準除錯路徑、關鍵不變量。
- **[除錯 runbook](troubleshooting.md)** — 症狀導向:票沒被處理、卡住、假完成、runner
  失敗、resume/冪等、Jira 降級、指令、花費、dashboard。
- **[可觀測性](design/observability.md)** — 證據地圖(journal/db/transcript/dashboard)、
  怎麼讀 journal、**42 種事件字典**、典型事件序列。
- **[LESSONS](lessons.md)** — 歷史踩坑全紀錄(症狀 → 根因 → 對策)。

## 研究與對照(Research)

開發過程的研究/實驗策展成「結論 + 比較」文章,與原始 deep-research 長文同放
[`docs/research/`](research/README.md):
[總體研究](research/runtime-control-plane.md) ·
[後端 A/B/C 對照](research/backend-abc.md) ·
[Crash→Resume](research/crash-recovery.md) ·
[Jira 整合設計](research/jira-integration.md) ·
[對照 qm 平台](research/qm-comparison.md) ·
[總覽](research/README.md)

## 深入設計

`docs/design/` 有各子系統的機制細節:
[生命週期](design/lifecycle.md) ·
[模組架構](design/architecture.md) ·
[Workspace 佈建](design/workspace.md) ·
[互動服務(HIL 人機介面)](design/interaction.md) ·
[可觀測性](design/observability.md) ·
[執行隔離](design/isolation.md) ·
[冪等](design/idempotency.md) ·
[熱重載](design/hotreload.md) ·
[transcript](design/transcript.md)

## 一句話世界觀

**Jira = 對外的工作日誌 / System of Record**;Agent 以「員工」身分接單 → 做事(後台)
→ 更新進度 → 回報成果讓人評分關單。真正的工作與細節在後台(workspace = 工作台;
dashboard/transcript = 完整飛行記錄器),Jira 只承載策展後的摘要/決策/結果/連結。
