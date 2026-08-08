# qm(yc-software)vs ARCP harness — 對比與「加我的功能」effort 分析

> 對象:`github.com/yc-software/qm`(本機 `~/git/qm`),TypeScript multiplayer
> agent platform。與本專案的 Jira 驅動自動化 harness(`harness/`)對比,並分析
> 把我們的差異化功能(Jira 驅動、證據型停止、recovery/stall)加到 qm 的 effort。
> 事實基礎:README/AGENTS/deploy-directory + Explore agent 行號級調查(附錄)。
> 2026-08-04。開源碼分析,無內部資訊。

## 0. 一句話

qm 是**面向公司的人驅動共享 agent 平台**(Slack+web、per-scope 隔離、生產級
Postgres/microVM/多雲);我們是**事件驅動的自動化 harness**(Jira ticket→證據
驗證→無人 resolve)。兩者架構哲學驚人相似(多 harness 可插拔 + durable core +
substrate 可換),但**qm 缺我們的核心差異化:證據型停止/grader、三態 outcome、
外部 ticket 攝入、stall watchdog**——因為它是人交互,不是自動化驗證。

## 1. 定位對比

| | qm | ARCP harness(我們) |
|---|---|---|
| 目的 | 公司共享 agent,員工在 Slack/web 協作 | Jira 事件驅動,ticket 自動處理無人化 |
| 觸發 | 人的 Slack/web 訊息 + cron/monitor | Jira ticket 輪詢 + routing |
| 成熟度 | 生產級(Postgres/pg-boss、docker/fly/aws、microVM) | 研究級(SQLite、seatbelt、單機) |
| 介面 | 完整 web UI + admin + portal + Slack | detail page + Jira comment |
| 平台特性 | scoped memory/keychain/web apps/crons/skills/多租戶 | 無(專注自動化) |

## 2. 架構收斂(驚人相似 — 各自獨立得出)

| 設計 | qm | 我們 | 判讀 |
|---|---|---|---|
| 多 harness/backend 可插拔 | `Harness` 介面,`defineHarness` 註冊 Map,harness-router 路由(Pi/Codex/Claude/OpenCode) | backend 契約(rawcli/openhands-acp/openhands-server),profile 一行切換 | **同一哲學** |
| 結構化事件 out-of-band | `emit(entry)` sink 逐事件落 durable session | envelope + events.jsonl | 同 |
| 冪等 | `idempotency.once(fireKey)` + `runs.enqueue(dedupKey)` | comment watermark + issue_id 主鍵 | 同 |
| 持久 run 佇列 | Postgres leased queue + reaper + heartbeat | poller + SQLite store(非佇列) | qm 是生產版 |
| 中斷續跑 | resume-note(散文告知 agent 續) | transcript bootstrap 注入 | 同機制 |
| substrate 可換 | deployment-directory,每 substrate 介面後可換 | backend/grader/source 可插拔 | 同 |

**這本身是重要發現**:一個生產級多人平台和我們的研究 harness,在「可插拔 core +
durable + substrate 可換」上獨立收斂 —— 印證這些架構賭注是對的。

## 3. 逐功能:誰有誰缺

### qm 更強(我們缺、值得學)
- **持久化/recovery 基礎設施**:Postgres leased run queue + reaper 重排崩潰 run +
  worker heartbeat(3 拍失敗→abort)+ tool-output ledger(冪等重放)+ 14 天冪等。
  遠比我們 SQLite+ServerManager 生產級。**這是 qm 最成熟的一塊**。
- **sandbox**:local-docker / sprites(resident-disk)/ aws microVM,per-scope
  durable,egress enforcement。我們只有 seatbelt/codex(無 docker)。
- **平台特性**:Slack/web UI、scoped memory、keychain、web apps、admin、多租戶。我們零。
- **harness 抽象成熟度**:capability profile(abort/steer/images…)、5 個 harness、
  安全螢幕(untrusted content 分類)。
- **生產部署**:docker/fly/aws + security screen proxy。

### 我們更強(qm **缺** — 我們的差異化 IP)
- **證據型停止/grader** —— **qm 完全沒有**。qm 的「completed」= 有沒有產出
  (`deriveTurnOutcome`:reply/attachment/issue 存在即 completed);task「completed」
  由 **agent 自己 tool call 設定**;唯一的 judge 是 LLM 判斷要不要回應 ambient 閒聊,
  不驗證工作。**qm 信任 agent 自稱完成**——正是 v5「loop on evidence, not confidence」
  要打破的。我們的 grader(跑測試/檢查檔案,DONE 不過覆寫 FAILED)是核心差異化。
- **三態 outcome(UNKNOWN)** —— qm 有 ok/failed/refused(投遞狀態),無
  SUCCESS/FAILURE/**UNKNOWN** 工作判定。
- **外部 ticket/webhook 攝入** —— qm 只硬接 Slack+web(live)+ cron/monitor(內部);
  `"webhook"` 只是 provenance 字串,**沒有任何東西攝入它**,無 Jira/generic-ticket connector。
- **stall/no-progress watchdog** —— qm 只有硬 wall-clock abort,**無 reset-on-progress**;
  最接近的是 lease-loss heartbeat(進程死)。
- **verify→fail→retry 迴路** —— qm 的 retry 只針對崩潰/lease(maxAttempts=3),
  **不因「工作沒驗過」重試**。我們的 dispatcher 就是這個迴路。

## 4. 「把我要的功能加到 qm」的 effort 分析

| 功能 | effort | 怎麼加 | 優 | 缺 |
|---|---|---|---|---|
| **Jira 驅動攝入** | 低-中 | qm 的 `runTrigger`/`surface`/`runs.enqueue` 是 source-agnostic;寫一個 Jira adapter 建 `TurnRequest`(surface="jira" + fireKey=issue_id+comment_id),仿 cron scheduler 輪詢 | 免費複用 qm durable queue+冪等+lease | qm turn 模型是**對話式**(reply-centric),非 ticket 生命週期(我們 outer loop 的 create/resume/handoff/三態 pending);要把 ticket 狀態機映射上去 |
| **grader/證據型停止** | **中-高** | qm **無 post-turn 驗證 hook**;要加 verify 步驟(跑測試/檢查檔案)+ verify 驅動的 retry(有別於崩潰 retry)+ 給 outcome 加 verify 判定 | 我們 grader 已證跨 runtime(A/B/C),可移植 | **最大的概念衝突**:qm 信任 agent、對話式;加 grader = 加一整套 qm 沒有的 verify→retry 迴路 + 三態,逆著 qm 的紋理 |
| **stall watchdog** | 低-中 | qm `HarnessTurnInput` **已有 `onProgress` sink**(§附錄)→ 接一個 no-progress 計時器,無進展→abort(像 wall-clock)。progress 訊號現成! | qm 已有 progress 訊號,接線容易 | 需和既有 wall-clock/lease heartbeat 協調 |
| **crash-resume** | **零(qm 已更強)** | 不用加;qm 的 leased queue+reaper+soft resume 比我們完整 | — | 我們 transcript 三段梯度可補一階,但非必要 |

## 5. 判斷與建議

1. **互補而非競爭**:qm = 生產平台基礎設施(durability/sandbox/多租戶/UI)我們缺;
   我們 = 驗證+自動化語意(grader/三態/Jira/stall)qm 缺。
2. **若目標是「生產級 Jira 自動化」**:qm 免費給你 durable/sandbox/多雲部署,但你要
   自己加**驗證層**(grader/證據型停止/verify-retry)—— 而那正是 qm 沒有、我們已驗證
   過的硬骨頭。**我們 harness 的 grader/dispatcher/三態就是要搬到 qm 基礎設施上的 IP。**
3. **effort 總結**:Jira adapter(低)、stall watchdog(低,onProgress 現成)、
   grader+verify-retry+三態(中-高,逆紋理)、ticket 生命週期 outer loop(中);
   recovery 免費(qm 更強)。**難的不是接線,是把「證據型停止」這個 qm 缺的語意
   植入一個信任 agent 的對話式平台。**
4. **最深的洞察**:qm 這個生產級多人平台**仍然沒有證據型驗證**(因為它人驅動,
   人就是驗證者)——這反向確認**我們的差異化是真的**:自動化無人場景才非要 grader
   不可。我們的 grader 是可以帶去任何地方的核心資產。

## 附錄:qm 關鍵事實(Explore agent 行號級,github.com/yc-software/qm)

- 觸發 chokepoint:`src/triggers/run-trigger.ts` `runTrigger`(135),`TriggerSpec`(33-52),
  `idempotency.once(fireKey)`(191)。cron `src/cron/scheduler.ts`、monitor
  `src/monitors/monitor-poller.ts`、wake `src/wake/wake.ts`。live 源只有 Slack
  (`src/api/slack-core-client.ts`)+ web(`src/api/app-turn.ts`)。`BackgroundWakeTrigger`
  含 `"webhook"` 字串但無接收器。
- 無 grader:`src/core/turn-outcome.ts` `deriveTurnOutcome` completed=產出存在;
  task 由 agent tool `transitionTask`(`codex-harness.ts:212`)設;唯一 judge 是
  `src/surface-cache/ambient-judge.ts`(LLM,回不回應)。
- recovery:`src/runs/run-store.ts`(leased queue)、`reaper.ts`、`worker.ts`
  (heartbeat,LEASE_LOST_CONSECUTIVE=3)、`src/core/turn-resume.ts`
  (`findTrailingPartialTurn`+resumeNote)、`src/runs/tool-ledger.ts`、
  `src/idempotency/idempotency-store.ts`(`once`)。maxAttempts=3。
- stall:無 no-progress watchdog;`pi-harness.ts:918` `raceTurnWallClock` 硬 wall-clock;
  worker heartbeat 是最接近的 wedged 偵測。`HarnessTurnInput.onProgress`(harness.ts:84)。
- harness 介面:`src/harness/harness.ts:167` `Harness`,唯一必須 `runTurn`(133);
  事件經 `emit()`(75)out-of-band;profile capabilities(155-161);router
  `harness-router.ts`。
- sandbox:`src/sandbox/sandbox.ts:131` `Sandbox`;backends local-docker/sprites/
  aws-microvm;per-scope durable,`sandbox-routing.ts` `routeFor(scopeId)`;
  egress enforcement。
