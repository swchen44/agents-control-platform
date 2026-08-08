# 研究與對照(Study)

開發過程中的研究、實驗與對照,策展成**「結論 + 比較」**的文章 —— 每篇回答一個
問題、給出結論、附對照表,並連回 `research/` 的原始長文與相關實作。原始研究報告
留在 [`research/`](../../research/)(dev-only,不入 wheel);這裡是它們的結論層。

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

> 想看「怎麼除錯」而非「怎麼設計」→ 見 [troubleshooting](../troubleshooting.md) 與
> [observability](../design/observability.md);想看子系統機制 → 見 [docs/design/](../index.md)。
