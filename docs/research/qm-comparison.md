# 對照 qm 平台:我們缺什麼、獨有什麼

> qm 是生產級的人驅動共享 agent 平台,ARCP 是事件驅動的自動化 harness;兩者在「可插拔 core + durable + substrate 可換」上獨立收斂,但 qm 缺我們的核心差異化 —— **證據型停止/grader**,因為它人驅動、人就是驗證者。

## 研究問題(為什麼拿 qm 對照)

`qm`(`github.com/yc-software/qm`)是一個 TypeScript 的 multiplayer agent platform:面向公司、由員工在 Slack/web 協作驅動,已達生產級(Postgres/pg-boss、docker/fly/aws、microVM、多租戶)。ARCP 則是 Jira ticket → 證據驗證 → 無人 resolve 的自動化 harness,目前研究級(SQLite、seatbelt、單機)。

拿 qm 對照的價值有兩層:

1. **驗證架構賭注**:一個生產級多人平台若和我們的研究 harness 在核心設計上獨立收斂,就反向印證這些賭注是對的。
2. **盤點缺口與差異化**:qm 成熟在哪(我們該抄),qm 缺什麼(我們獨有的 IP),把「加我要的功能到 qm」的 effort 攤開看清概念衝突在哪。

事實基礎是 qm 的 README/AGENTS/deploy-directory,加上 Explore agent 的行號級調查(原文附錄)。純開源碼分析,無內部資訊。

## qm 與 ARCP 對照(表格)

**定位與成熟度**

| 維度 | qm | ARCP(我們) | 結論 |
|---|---|---|---|
| 目的 | 公司共享 agent,員工在 Slack/web 協作 | Jira 事件驅動,ticket 自動處理無人化 | 互補,非競爭 |
| 觸發 | 人的 Slack/web 訊息 + cron/monitor | Jira ticket 輪詢 + routing | 我們獨有外部攝入 |
| 成熟度 | 生產級(Postgres/pg-boss、docker/fly/aws、microVM) | 研究級(SQLite、seatbelt、單機) | qm 遠勝,該抄基礎設施 |
| 介面 | 完整 web UI + admin + portal + Slack | detail page + Jira comment | qm 勝(非我們重點) |
| 平台特性 | scoped memory/keychain/web apps/crons/skills/多租戶 | 無(專注自動化) | qm 勝(非我們重點) |

**架構收斂(驚人相似,各自獨立得出)**

| 設計 | qm | 我們 | 判讀 |
|---|---|---|---|
| 多 harness/backend 可插拔 | `Harness` 介面 + `defineHarness` 註冊 + harness-router | backend 契約(rawcli/openhands-acp/openhands-server),profile 一行切換 | 同一哲學 |
| 結構化事件 out-of-band | `emit(entry)` sink 逐事件落 durable session | envelope + events.jsonl | 同 |
| 冪等 | `idempotency.once(fireKey)` + `runs.enqueue(dedupKey)` | comment watermark + issue_id 主鍵 | 同 |
| 持久 run 佇列 | Postgres leased queue + reaper + heartbeat | poller + SQLite store(非佇列) | qm 是生產版,該抄 |
| 中斷續跑 | resume-note(散文告知 agent 續) | transcript bootstrap 注入 | 同機制 |
| substrate 可換 | deployment-directory,每 substrate 介面後可換 | backend/grader/source 可插拔 | 同 |

**核心語意差異(誰有誰缺)**

| 維度 | qm | ARCP(我們) | 結論/該不該抄 |
|---|---|---|---|
| 完成判定 | 信任 agent 自稱完成(`deriveTurnOutcome`:reply/attachment/issue 存在即 completed;task 由 agent tool call 設) | grader 跑測試/檢查檔案,DONE 不過覆寫 FAILED | 我們獨有,不抄 —— 反成 qm 該學我們 |
| outcome 態 | ok/failed/refused(投遞狀態) | SUCCESS/FAILURE/**UNKNOWN**(工作判定) | 我們獨有 |
| 外部 ticket 攝入 | 只硬接 Slack+web + cron/monitor;`"webhook"` 只是字串無接收器 | Jira 輪詢 + routing | 我們獨有 |
| stall 偵測 | 只有硬 wall-clock abort;無 reset-on-progress | stall/no-progress watchdog | 我們獨有 |
| verify→retry | retry 只針對崩潰/lease(maxAttempts=3),不因「工作沒驗過」重試 | dispatcher 的 verify→fail→retry 迴路 | 我們獨有 |
| recovery 基礎設施 | Postgres leased queue + reaper + heartbeat(3 拍失敗→abort)+ tool-output ledger + 14 天冪等 | SQLite + ServerManager | qm 遠勝,**該抄** |
| sandbox | local-docker / sprites / aws microVM,per-scope durable,egress enforcement | seatbelt/codex(無 docker) | qm 勝 |

## qm 值得抄的(條列 + 為什麼)

- **持久化/recovery 基礎設施 —— qm 最成熟的一塊**:Postgres leased run queue + reaper 重排崩潰 run + worker heartbeat(`LEASE_LOST_CONSECUTIVE=3` 拍失敗→abort)。我們的 poller + SQLite store 不是佇列,崩潰 run 無人重排。這是接線就能白拿的生產級能力。
- **tool-output ledger(冪等重放)**:`src/runs/tool-ledger.ts` 讓工具輸出可冪等重放,配合 14 天冪等視窗。我們目前只有 comment watermark + issue_id 主鍵層級的冪等,缺工具層。
- **sandbox substrate**:local-docker / sprites(resident-disk)/ aws microVM,per-scope durable + egress enforcement。我們只有 seatbelt/codex、無 docker。
- **harness 抽象成熟度**:capability profile(abort/steer/images…)、5 個 harness、untrusted content 分類的安全螢幕。可作為我們 backend 契約演進的參照。

判斷依據(原文):qm 在「durability/sandbox/多租戶/UI」上是生產平台基礎設施,正是我們缺的;把我們的驗證語意搬到 qm 這種基礎設施上,才是「生產級 Jira 自動化」的完整形態。

## ARCP 獨有的差異化(qm 完全沒有 —— 我們的 IP)

- **證據型停止/grader —— qm 完全沒有**。qm 的「completed」= 有沒有產出;task「completed」由 agent 自己 tool call(`transitionTask`)設定;唯一的 judge 是判斷要不要回應 ambient 閒聊的 LLM,不驗證工作。**qm 信任 agent 自稱完成** —— 正是 v5「loop on evidence, not confidence」要打破的。我們的 grader(跑測試/檢查檔案,DONE 不過覆寫 FAILED)是核心差異化。
- **三態 outcome(UNKNOWN)**:qm 只有 ok/failed/refused 的投遞狀態,無 SUCCESS/FAILURE/**UNKNOWN** 的工作判定。
- **外部 ticket/webhook 攝入**:qm 只硬接 Slack+web(live)+ cron/monitor(內部);`"webhook"` 只是 provenance 字串,**沒有任何東西攝入它**,無 Jira/generic-ticket connector。
- **stall/no-progress watchdog**:qm 只有硬 wall-clock abort,**無 reset-on-progress**;最接近的只是 lease-loss heartbeat(偵測進程死,不是無進展)。
- **verify→fail→retry 迴路**:qm 的 retry 只針對崩潰/lease,**不因「工作沒驗過」重試**。我們的 dispatcher 就是這個迴路。

**最深的洞察(原文)**:qm 這個生產級多人平台**仍然沒有證據型驗證** —— 因為它人驅動,人就是驗證者。這反向確認我們的差異化是真的:**自動化無人場景才非要 grader 不可**,而 grader 是可以帶去任何地方的核心資產。

## 對 ARCP 的影響(這些對照如何進了 BACKLOG)

這輪對照把「該抄什麼、該守什麼」轉成具體 backlog 方向:

- **A1 — Postgres/durable run queue**:對照最明確的缺口。qm 的 leased queue + reaper + heartbeat 是它最成熟的一塊,我們的 SQLite store 不是佇列、無崩潰重排。方向是把 run store 升級成有 lease/heartbeat/reaper 的持久佇列,把「進程死掉的 run」自動重排。
- **A2 — tool-output ledger(冪等)**:抄 qm 的 tool-ledger,把冪等從 comment/issue 層下沉到工具輸出層,支援冪等重放,配合較長的冪等視窗。
- **C1 — grader / 證據型停止**:對照確認這是我們**不能丟、且無處可抄**的核心 IP,反而要保護並強化。qm 完全沒有 post-turn 驗證 hook;我們的 grader(跑測試/檢查檔案 + DONE 覆寫 + 三態 + verify→retry)就是要搬到 qm 式基礎設施上的資產。

配套結論(effort 判讀,原文 §4):若反過來把我們的功能加到 qm,Jira adapter(低,`runTrigger`/`surface`/`runs.enqueue` 是 source-agnostic)、stall watchdog(低,`HarnessTurnInput.onProgress` 現成)都容易;真正難的是 grader + verify-retry + 三態(中-高,**逆著 qm 「信任 agent、對話式」的紋理**),以及 ticket 生命週期 outer loop(中);crash-resume 則不用加(qm 更強)。這條 effort 曲線正好對應我們 backlog 的優先序:**先補基礎設施(A1/A2,別人已驗證好抄),守住並移植語意層(C1,別人沒有)。**

## 原始出處

- [research/2026-08-qm-comparison.md](2026-08-qm-comparison.md) — qm(yc-software)vs ARCP harness 對比與 effort 分析,含 Explore agent 行號級附錄(2026-08-04,純開源碼分析)。
