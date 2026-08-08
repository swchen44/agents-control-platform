# 研究與對照(Research)

開發過程中的研究、實驗與對照。本資料夾**同時收兩層**:

- **結論層**(下表):策展成「結論 + 比較」的文章 —— 每篇回答一個問題、給出結論、
  附對照表 + 「對 ARCP 的影響」,並連回同資料夾的原始長文。**先讀這層。**
- **原始長文**:`2026-*.md` 那幾份 deep-research 報告(v2/v3/v5/整合/openhands/qm/roadmap),
  含完整出處與推導,是結論層的依據。

## 結論層(策展文章)

| 主題 | 一句話結論 |
|---|---|
| [總體研究:Agent Runtime / Control Plane](runtime-control-plane.md) | headless coding agent 缺的是「跨 CLI 一致的可觀測 + 可控制」那一層;ARCP 做這層,不發明編排。 |
| [後端路線 A / B / C 對照](backend-abc.md) | 同任務同 grader 實跑後 **C(rawcli)集大成勝出**:保真追平 A、語意比誰都乾淨、保有控制窗口與可視化,甩掉 ACP 的粗粒度。 |
| [Crash → Resume 崩潰復原](crash-recovery.md) | claude/codex 的 2×2 崩潰矩陣**皆實測全過、不重工**;真教訓是「別信 exit code / 事件流 / agent 自稱 done」→ 復原要建在證據上。 |
| [Jira Harness 整合設計](jira-integration.md) | 用**欄位所有權模型**(每欄單一 writer)把 agent 接進 Jira 既有狀態機;Cloud 髒細節全關進單一 source adapter;三態 outcome。 |
| [對照 qm 平台](qm-comparison.md) | 生產級的 qm 在**基礎設施**(durable queue / ledger / sandbox)勝我們該抄;但它**沒有證據型停止/grader** —— 那是我們的核心 IP。 |

## 這些研究如何影響了實作

- **證據型停止(grader)** 貫穿 [crash-recovery](crash-recovery.md)(SIGTERM rc=0 假完成把它從加分項升級為必要)與 [qm 對照](qm-comparison.md)(qm 完全沒有 → 我們獨有的差異化)。
- **rawcli 主線 + envelope 契約跨 backend** 來自 [A/B/C 對照](backend-abc.md)。
- **jira_source 單檔封裝 + statusCategory/issue-type-id + 三態** 來自 [jira 整合](jira-integration.md)。
- **該抄的基礎設施**(Postgres leased queue、tool-output ledger)來自 [qm 對照](qm-comparison.md) → 已進 [BACKLOG](../../BACKLOG.md) A1/A2。

## 原始長文(deep-research 報告)

策展文章的依據;要看完整推導/出處時讀這些:

- [2026-08-agent-runtime-control-plane-research-v3.md](2026-08-agent-runtime-control-plane-research-v3.md) — v3 總體研究(最新;收斂到工程可執行 + PoC 實測)
- [2026-07-agent-runtime-control-plane-research.md](2026-07-agent-runtime-control-plane-research.md) — v2 前版(市場缺口/可行性/對抗式驗證)
- [2026-08-jira-agent-harness-design-v5.md](2026-08-jira-agent-harness-design-v5.md) — v5 Jira harness 設計(C1-C4 / D1-D10 / KPI)
- [2026-08-jira-harness-integration.md](2026-08-jira-harness-integration.md) — v5 × 實作整合分析
- [2026-08-abc-roadmap-analysis.md](2026-08-abc-roadmap-analysis.md) — A/B/C 三路線策略與可行性
- [2026-08-openhands-acp-claude-code-lifetime.md](2026-08-openhands-acp-claude-code-lifetime.md) — OpenHands × ACP × Claude Code 生命週期讀碼研究
- [2026-08-qm-comparison.md](2026-08-qm-comparison.md) — 對 qm 平台的行號級對照

> 想看「怎麼除錯」而非「怎麼設計」→ 見 [troubleshooting](../troubleshooting.md) 與
> [observability](../design/observability.md);想看子系統機制 → 見 [docs/design/](../index.md)。
