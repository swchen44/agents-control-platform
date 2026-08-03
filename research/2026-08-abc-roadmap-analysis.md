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

**唯一真正未知數**:SDK Agent 基類介面深度 → ✅ **spike 已完成(2026-08-03,
`examples/openhands-acp-poc/spike_rawcli_agent.py`,4/4 PASS)**:
- 契約:`AgentBase` 唯一抽象方法 `step()`;`llm` 欄位用 dummy(ACPAgent 前例);
  `init_state` 可覆寫跳過 SDK 工具解析(CLI 自帶工具)
- **`Conversation(agent=…)` 接受外部子類——C 期確定不用 fork**,RawCLIAgent
  活在自己的套件
- `step()` 內 `on_event(<SDK 事件>)` 正常進 event 體系
- **真 `claude -p` print mode 已在 OpenHands Conversation 內跑通一輪**
  (~80 行最小雛形;正式版把 arcp_poc/drivers.py 的 stream-json 解析搬入即可)

### B 期執行形態澄清(2026-08-03 問答)

B 的執行單元**不是** `claude -p`:鏈為 ACPAgent → adapter 子行程(node)→
adapter 內嵌 Claude Code(headless)。同樣無人互動,但呼叫形態與 C 不同——
`claude -p` print mode 是 C 期才出現,粒度差(14 vs 248)的根源即在此。

**已知要扛的(B 期)**:粗粒度 watchdog 降級(分鐘級任務可接受;由 A 對照
量化)、bypassPermissions 治理押 workspace 隔離(v5 D6 專案隔離因此必要)、
codex quota 至 8/31。

## 3.5 B+ 修訂:視覺化/traceability 收割(使用者方向 2026-08-03)

使用者欲在 B 期收割 OpenHands 的視覺化管理與 traceability。判定:**可行且
投資可帶進 C**——C=RawCLIAgent 活在 SDK 內,Conversation/event 體系與
agent-server 不動,GUI/trace 在 C 期照用且事件更細(14→248 級)。

- **前置**:inner runner 從 in-process 換 **agent-server 模式**(POST
  /api/conversations + WS,v3 §6.3;envelope 契約不變)——同時完成 backlog
  的 agent-server 行為驗證 spike。
- **收割形態**:(a) Agent Canvas GUI 零開發,conversation 視角;
  (b) agent-server REST/WS + 自建 detail page(v5 §4.7),可拼上我們的
  L0/L2 → 完整 traceability 視角。
- **邊界**:OpenHands 只管 conversation 級(L1/L3);L0(ticket)/L2
  (envelope/驗證/成本)仍是 harness journal——互補不是替代。
- **C 期待驗點**:agent-server 由 server 端實例化自訂 RawCLIAgent
  (註冊/反序列化)——C spike 2,半天。

修訂後路線:**B(已完成 M1-M3)→ B+(agent-server 模式 + 視覺化收割)→ C**。

**C 期已實作並實跑(2026-08-03,`harness/arcp_rawcli/` + PLAN_C.md)**:
C.0 gate(server 端可實例化自製 agent,集大成確認)→ C.1(RawCLIAgent 跑通)
→ C.2(細粒度:蒸餾 10 事件 + 原生 94 保真)→ C.3(接進 harness,backend=rawcli,
dispatcher 零改動,SCRUM-11 SUCCESS)→ C.4(crash→resume,--resume,對照 A 矩陣)
→ C.5(A/B/C 三方對照,COMPARISON §6:C 集大成經實跑實證——保真≈A、語意乾淨勝 B、
中途控制窗口 B 缺、可視化 A 缺)。C 的困難知識全來自 A 期,無新架構風險。

## 4. 與 v5 P0-P4 的關係

時序相容:P0/P1 的 inner/outer loop 在 B 期建、天然為 C 服務(outer loop
100% 存活)。v5 的執行單元契約(§4.3 `--bare`/JSON schema/預算)在 C 期落地;
B 期先用 ACP 版執行單元頂替,介面照守則 2 隔離。
