# Jira Harness 設計文件(v5)× ARCP 實測 — 整合分析

> 使用者另行完成的「Jira 驅動的 Agent Harness — 選型研究報告與設計 Prompt v5
> (2026-08-02)」(Google Docs,含內部資訊故原文不入本 public repo;連結存於
> session memory)。本文把該設計與本 repo 兩天的實測成果對齊:哪些決策被我們
> 的實驗**證實**、哪些設計**比我們現有實作更完整該採用**、哪些開放問題**我們
> 已經回答**、以及據此修訂的 roadmap。2026-08-03。

## 0. 一句話結論

v5 的架構(OpenHands SDK 當 harness 骨架 + raw headless CLI 當執行單元、
**不走 ACP**)與本 repo COMPARISON.md §5 的「分工」結論**殊途同歸且更進一步**:
它不是在 A 與 B 之間選邊,而是**各取所長的合成**——OpenHands 拿它走過的坑
(workspace 抽象、event-sourced 持久化、Agent Server REST/WS),執行與 trace
仍走 raw CLI(保住我們實測的 248 事件細粒度與控制窗口)。兩份研究互相驗證。

## 1. v5 決策 × ARCP 實測證據對照

| v5 決策/主張 | ARCP 實測證據 | 判定 |
|---|---|---|
| **不走 ACP**(理由:只買到登入態+request_permission,卻承擔全部故障面) | 粒度 14 vs 248(~18:1)、批次執行**無中途 kill 窗口**、session 綁 cwd(workspace 搬家即 `No conversation found`)——皆實測 | ✅ **實驗證實**,且我們補了它引用 issue 之外的第一手數據 |
| ACP 故障模式 #2:quota 觸發後 session 壞而不死 | 我們實測到姊妹現象:codex quota 觸發時 B 路收到**結構化 ConversationErrorEvent**(SDK 有接住);raw 路 SIGTERM rc=0 假完成 | ⚠️ 部分證實;SDK 層比裸 ACP client 處理得好 |
| session/load 依賴 server 側儲存、cwd 為 key | 實測:adapter SIGKILL 後 session/load 可重接(同 id、truly_resumed);cwd-key 陷阱在 raw 路實測 | ✅ 證實(含它沒說的:重接是真的可用) |
| 「Session source of truth 永遠是自己的 event log」 | 我們的 journal→transcript 降級 resume 就是此原則的實作,且已 live 驗證(workspace 搬家後救回) | ✅ 雙方獨立得出同一設計 |
| 證據型停止、"loop on evidence" | grader + supervisor 覆寫 FAILED,已 live 驗證(codex rc=0 假完成被抓) | ✅ 已實作,v5 補了 UNKNOWN 三態(見 §2) |
| headless 免 OAuth 需 `--bare` + API key(公司 API 合約情境) | 我們實測的是**訂閱登入**情境(ACP adapter 與 raw 都直接可用);`--bare` 未實測 | ⚠️ 情境不同:v5 走公司 API 合約。`--bare` 旗標行為要照 v5 自己的警告實測驗證 |

## 2. v5 比 ARCP 現有實作更完整、該採用的設計

1. **UNKNOWN 三態**(抄 Hermes):我們的 grader 是二值 pass/fail;v5 要求
   SUCCESS / FAILURE / **UNKNOWN**(無法證明副作用是否發生——例如 kill 子行程後)
   → `pending:unknown` 只能人解除。**採用**:grader/Verdict 與 recovery loop 的
   outcome 加第三態;「kill 後」正是我們矩陣的日常,現在一律當 FAILURE 其實越權。
2. **resume 三層 fallback 的中間階**:v5 在 native resume 與 event-log bootstrap
   之間插了 `--resume <id> --fork-session`(保歷史、新 ID)。我們的梯度缺這一階。
   **採用**:recovery ladder 從三階變四階,fork 階可解「原 session 可讀但不可續寫」
   的情境。待實測。
3. **workspace health check(resume 前必做)**:路徑可寫、git 狀態可讀、未提交
   變更量門檻、TICKET.md 一致性。我們的 recovery loop 直接 resume 不檢查。**採用**。
4. **issue_id(數字)當主鍵**,ticket key 會因 project move 改變。我們的 PoC 用
   issue key。**採用**(小改,晚改動全部呼叫點)。
5. **欄位所有權模型**(每欄位單一 writer)+ comment 指令通道(watermark、
   commenter 白名單、冪等、ack 回覆)。比我們 escalation PoC 的單向回寫完整一個
   量級。**採用為 P2 規格**。
6. **L0-L3 四層 trace 對齊** + Trace completeness 100% 為 P1 唯一硬 KPI。
   我們有 L1-L3 的雛形(journal/snapshot/stream),缺 L0(ticket 層)與完整性 CI。
7. **§10 KPI 框架**(北極星 first-pass Close rate、效率指標必配制衡指標、
   Goodhart 防護表、明確不量清單)。ARCP 完全沒有 KPI 層。**整份採用**。
8. **雙閘門併發**(max_running 資源閘 / max_awaiting_close 審查閘)。

## 3. v5 的開放問題,ARCP 已有答案

v5 §2.2 留了四個「AionUi/ACP 對照 spike(P4,1-2 天)」要驗的問題——
**openhands-acp-poc 已經回答其中三個**,P4 spike 可縮減:

| v5 問題 | ARCP 實測答案 |
|---|---|
| CLI 子行程死掉時 ACP 怎麼表現 | SIGKILL adapter → session store 完好,`session/load` 重接同 session(兩輪 4/4);quota 錯誤以 ConversationErrorEvent 結構化浮出 |
| session 與 workspace 怎麼綁 | ACP 以 cwd 為 key 的行為與 raw 一致;SDK 側有 `acp_resume_session_id` 顯式解耦 |
| 多 workspace 併行行為 | 未測(v5 若走 agent-server 形態,我們的 backlog 項「agent-server 對照」即此) |
| 執行中權限閘門 UX | 未測(需 AionUi 或 agent-server confirmation_policy) |

另外 v5 §6 陷阱清單與我們的實測陷阱互補:它的 #1(cwd 綁定)我們有實測版;
我們的「筆電睡眠凍結計時器」「npx 預熱」「quota 跨路線共用」「codex 工具粒度
變異」它沒有——兩份清單應合併維護。

## 3.5 使用者的 agent-server 讀碼研究(2026-08-02)帶來的增量

使用者另有一份行號級讀碼研究(`~/git/openhands/docs/research/
openhands-acp-claude-code.md`,對象為本機 clone,內容為開源碼分析無內部資訊),
把 ARCP backlog「agent-server 模式對照」的**讀碼部分做完了大半**。關鍵事實:

1. **多 workspace 併發原生支援**:單一 agent-server 管 N conversation(dict
   UUID→EventService),每個 ACP conversation 一個獨立 adapter 子行程;workspace
   是 per-conversation 參數(`new_session(cwd=…)` 綁定)。→ v5 §2.2 第四問有
   架構級答案,待行為實測。
2. **Resume 是常態機制不只是 recovery**:conversation 閒置 20 分鐘 Evict
   (殺子行程、存 `base_state.json`),再存取 rehydrate + `load_session`。
   ARCP 實測驗證的重接路徑正是其日常回收路徑——可靠性的間接背書再加一層。
3. **cwd 變更即放棄 resume**:`acp_session_id` 持久化時記錄建立時 cwd,不符即
   開新 session——ARCP workspace 搬家實測到的陷阱,OpenHands 在 client 層繞開。
4. **權限一刀切**:ACP 握手後 `set_session_mode("bypassPermissions")`(codex 用
   agent-full-access)。B 路權限治理完全押在 workspace 隔離,無 A 路 permission
   matrix 的細粒度——v5 權限設計(D6 專案隔離)的重要輸入。
5. **「免 API key」機制解釋**:桌面版刻意沿用本機 `~/.claude` 憑證
   (`acp_isolate_data_dir` 預設關);多實例搶 `~/.claude` 狀態(config/快取/鎖檔)
   時要開此開關 + 憑證注入優先序 secret_registry > os.environ。→ v5 部署清單應加。
6. **skill 注入正解與 v5 吻合**:`ACPAgentProfile` 無 skill 欄位;per-workspace
   `.claude/skills/`(原生)或 `.openhands/skills/`(prompt suffix,首輪注入一次)
   才是差異化路徑——v5 `inject_as: claude_skills` 設計正確。
7. **改造點行號級定位**(補 COMPARISON §5 可行性帳):細粒度瓶頸在 adapter 的
   `session_update` 通知種類(token/thought/tool call/usage 四類);OpenHands 側
   橋接在 `_OpenHandsACPBridge`(acp_agent.py:1041)。fork 評估有了確切座標。
8. 部署細節:uvx 現拉(首啟數分鐘,ready timeout 10 分)、`X-Session-API-Key`
   驗證、WS `/sockets/events/{id}`、startup 90s / prompt idle 1800s 硬限。

**backlog 修訂**:「agent-server 模式對照」從「讀碼+實測」縮為**行為驗證 spike**
(併發 N conversation 實跑、閒置 Evict→rehydrate 實測、與 Jira pipeline 的
併發閘門 D10 對接評估);v5 P4 spike 進一步縮到只剩「執行中權限閘門 UX」一項。

## 4. 架構修訂:ARCP roadmap 對齊 v5 的 P0-P4

v5 的雙層迴圈(outer=ticket 生命週期/Graph、inner=workspace+skill/Harness+Loop)
是 ARCP 現有元件的**正式化重組**,對映與缺口:

| v5 階段 | 內容 | ARCP 現況 | 缺口 |
|---|---|---|---|
| P0 | inner loop 跑通:YAML profile → workspace → CLI(JSON 輸出)→ verify → 三態 | supervisor+grader+journal ≈ 80% | YAML profiles、`--bare`/JSON-schema 呼叫契約、**UNKNOWN 三態**、預算控制 |
| P0 | L2 trace + UNKNOWN 觸發 + trace 完整性 CI | journal/snapshot 有 | .result.json 封套、四層盤點 CI |
| P1 | issue_id 對映表(SQLite/WAL)+ health check + create/resume 分流 | workspace.py + snapshot 雛形 | SQLite 對映表、health check、issue_id 主鍵 |
| P1 | outer loop:輪詢+watch+routing+notify_only 灰度 | jira_watcher 雛形(輪詢/去重/dispatch) | watch 事件種類、YAML routes、灰度模式 |
| P1 | resume 三層 fallback + event log 持久化 | **四階裡有三階已實測**(native/transcript/rerun) | `--fork-session` 階實測 |
| P2 | Jira 欄位寫入+comment 指令通道+detail page+external_change_policy | escalation.py 雛形(denial→開票/回寫) | 所有權模型、watermark/冪等/白名單、detail page |
| P3 | L3 stream trace、liveness、Docker workspace、codex 第二執行單元 | stream trace/stderr 落地、codex driver **已有** | liveness probe、Docker(OpenHands workspace 抽象接入點) |
| P4 | ACP 對照 spike | **大半已完成**(見 §3) | 僅剩權限閘門 UX 與併行行為 |

**骨架選擇的整合判定**:v5 選 OpenHands SDK 當骨架的三個理由(workspace 抽象
/Docker、event-sourced 重放、Agent Server REST/WS)正是 ARCP backlog 裡
「agent-server 對照」要驗的東西;而 v5 保留 raw CLI 執行單元,等於保住 ARCP
實測認定的 A 路優點。**兩者相容**:ARCP 的 supervisor/grader/recovery/escalation
可視為 v5 inner loop 的參考實作與實驗場;正式實作按 v5 §8 起手 prompt 執行時,
本 repo 的實測事實(§1 表)與 fixtures 是「動工前逐條重新確認」的現成材料。

## 5. 待使用者決策(下次動工前)

1. 正式實作的 repo:本 repo 繼續(public,需去識別)or 新 private repo?
2. P0 起手時本 repo 的 PoC 元件是「直接演進」還是「當參考另起爐灶」?
3. `--bare` + 公司 API 合約情境 vs 目前訂閱登入情境,實驗環境何時切換?
