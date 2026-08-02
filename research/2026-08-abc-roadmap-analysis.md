# A/B/C 路線策略分析 — short term B、long term C、A 作對照(2026-08-03)

> 使用者策略(已定):**short term 用 B 先做出可執行的;long term 用 C;
> A 留作對照**。本文定義三案、GAP、B→C 成長路徑存活率、可行性判定與設計守則。
> 證據基礎:jira-agent-poc 實測(A)、openhands-acp-poc 實測(B)、
> v5 設計文件與 agent-server 讀碼研究(見 2026-08-jira-harness-integration.md)。

## 0. 三案定義

- **A — raw 對照組**:自寫 supervisor(現有 jira-agent-poc)+ 必要時補
  OpenHands 級功能。**角色:對照 harness 與參考實作,不下場當產品。**
- **B — short term**:OpenHands SDK + ACP adapter 包 claude/codex headless
  + Jira outer loop + resume。接受已知缺點,最快可執行。
- **C — long term**:在 OpenHands SDK 內寫 **RawCLIAgent**(與 ACPAgent 平行的
  Agent 實作):直接 spawn `claude -p`/`codex exec`、解析原生 stream-json、
  發**完整細粒度事件**進 OpenHands event-sourced 體系。

### C 的關鍵澄清:「event 更多」的兩條路

細粒度瓶頸實測定位在 **ACP 協定 + adapter**(細事件在 adapter 內部即丟棄,
OpenHands 側橋接 `_OpenHandsACPBridge` acp_agent.py:1041 只收四類通知):

- C1 fork `claude-agent-acp`(TS)→ 永遠跟上游、協定無承載欄位。**劣路,棄**。
- **C2 RawCLIAgent(採用)**:不碰 ACP。此即 v5 文件「OpenHands 骨架 + raw CLI
  執行單元、不走 ACP」的實作形態;所需困難知識(schema/終止語意/resume 梯度/
  陷阱)已全部在 A 期量測完畢——**搬運,不是探索**。

## 1. GAP 對照

| | A(對照) | B(short term) | C(long term) |
|---|---|---|---|
| 現況完成度 | 差異化層全實測(grader/recovery 梯度/escalation/transcript) | **執行鏈已實測跑通**(雙 CLI headless、resume、同 grader) | 0 行碼,知識完成度最高 |
| 到可執行的距離 | 遠(workspace 抽象/REST/持久化要自建 = 重造 OpenHands 走過的坑) | **最近:只差 outer loop,數天級** | 中:Agent 介面 + driver 移植,1-2 週級 |
| 已知缺點 | 重造 80% | 14 事件粗粒度(watchdog 降到工具呼叫級)、無中途控制窗口、bypassPermissions 一刀切、adapter 版本鏈 | SDK Agent 介面深度未探;OpenHands 版本演化快 |
| 事件粒度 | 248(實測) | 14(實測) | 目標 ≈ A 級(由 A 對照驗收) |

## 2. B→C 成長路徑存活率(策略可行性核心)

| B 期資產 | 存活 | 理由 |
|---|---|---|
| Jira outer loop 全部(routing/watermark/指令通道/欄位所有權/對映表) | ✅ 100% | 對接 Conversation API,不碰 agent 內部 |
| workspace/profile YAML | ✅ ~95% | 只換 agent 設定區塊 |
| grader / verify / 三態 outcome | ✅ 100% | 檔案系統真值,已證跨路線 |
| recovery 梯度 | ✅ 概念全存活 | native 階從 session/load 換 --resume,兩者皆已實測 |
| trace / KPI / detail page 消費端 | ✅ 介面存活 | C 期事件種類變多(守則 1 保前向相容) |
| ACP adapter 依賴 | ❌ 拋棄 | 本來就是要換的那層 |

### B 期三條設計守則(讓存活率成立的前提)

1. **消費端只依賴 OpenHands event stream + L2 result 封套**,不碰 ACP 原始物;
   事件消費者從第一天容忍未知事件種類。
2. **agent 設定在 YAML 獨立區塊**——B→C 換執行單元 = 換一個區塊。
3. **A 不下場**:jira-agent-poc 保持可跑,同任務對照當品質閘——
   B 期驗收「粗粒度夠不夠」,C 期驗收「事件是否補齊到 A 水準」。

## 3. 可行性判定

**可行,風險結構良好**:
- B 執行鏈已實測跑通(不是賭);
- B→C 遷移面窄且已知(只換執行單元一層);
- C 的難點知識已在 A 期付清(fixtures、陷阱清單、梯度、雙判據失效證據);
- A 對照零額外成本(已存在)。

**唯一真正未知數**:SDK Agent 基類介面深度 → C 動工前先做半天 spike
(讀 `Agent` 基類與 `ACPAgent.init_state/step` 契約)。

**已知要扛的(B 期)**:粗粒度 watchdog 降級(分鐘級任務可接受;由 A 對照
量化)、bypassPermissions 治理押 workspace 隔離(v5 D6 專案隔離因此必要)、
codex quota 至 8/31。

## 4. 與 v5 P0-P4 的關係

時序相容:P0/P1 的 inner/outer loop 在 B 期建、天然為 C 服務(outer loop
100% 存活)。v5 的執行單元契約(§4.3 `--bare`/JSON schema/預算)在 C 期落地;
B 期先用 ACP 版執行單元頂替,介面照守則 2 隔離。
