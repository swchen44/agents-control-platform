# 走一遍:CR → Jira 票 → triage 選 profile → claude code 跑完關單

> 用**一個具體場景**把整條路徑順一遍,看清 **label / status / `ticket_watch` /
> `ticket_session` / `config.yaml`** 這幾個東西各管什麼、怎麼串起來。讀完你應該能自己
> 對著 dashboard 與 journal debug。設計理由見 [lifecycle](design/lifecycle.md) /
> [architecture](design/architecture.md) / [selection](design/selection.md);症狀式排錯見
> [troubleshooting](troubleshooting.md)。

## 0. 先認識五個主角(誰管什麼)

| 主角 | 存在哪 | 誰寫 | 管什麼 | 會不會變 |
|---|---|---|---|---|
| **label** | Jira 票欄位 | **建票的人/程式**(人、CR-bridge、job) | **入場券**:決定要不要進場、走哪條 route | 系統**只讀不改**;做完不拿掉 |
| **status** | Jira 票欄位(待辦/進行中/完成…) | 人 or 系統 `transition()` | Jira 端的工作流狀態;**關單**靠它(轉 done) | 系統只在**關單/取消**時改 |
| **`ticket_watch`** | SQLite(harness) | poller 每輪 | **水位/快照**:看過的最大 comment id、上次 state/assignee、命中的 route 名 | 每輪更新 |
| **`ticket_session`** | SQLite(harness) | dispatcher/HIL/scoring | **派工狀態**:鎖定的 profile、attempts、outcome、cost、session_id | 派工全程 |
| **`config.yaml`** | repo(唯讀設定) | 人(改完 `POST /reload`) | **routes**(label→profile→on_match)+ **profiles**(agent 怎麼跑) | 熱重載才變 |

一句話總結三組關係:
- **label ↔ config.routes**:label 是入場券,`routes[].when.labels` 是驗票規則 →
  命中決定 `profile` 與 `on_match`。
- **status ↔ 生命週期**:Jira status 是**人看的**;harness 內部 6 態是從
  `ticket_session` 欄位**推導**的(不是 Jira status),見 §5。
- **`ticket_watch` / `ticket_session` ↔ 「處理到哪」**:label/status 都**不帶**「處理過沒」;
  那是這兩張表以 numeric `issue_id` 為 key 記的。

```
 CQ CR ──(bridge*)──▶ Jira 票 ──poller 撿票──▶ route 命中 ──create_or_resume──▶ dispatch
  BUGDB-1234           SCRUM-42                (config.routes)                      │
                       label=filechain                                             ▼
                       status=待辦                                          首次派工 triage(select)
                                                                          選 profile → 鎖進 session
                                                                                    │
                                                                                    ▼
                                                                        provision ws → 呼叫 claude code
                                                                        → envelope 契約 → grader 判 outcome
                                                                                    │
                                                                      SUCCESS ──▶ 貼交付物 → HIL(End)/auto_close
                                                                                    │
                                                                      transition done ──▶ closed ──(CQ 回寫*)
 * = 設計已定、程式未接(見 §7)
```

## 1. 場景設定

- **CR**:ClearQuest 有一張 `BUGDB-1234`「登入頁在 Safari 崩潰」。
- **config.yaml**(節錄——這是**你跑的**設定,不是 example):

  ```yaml
  outer_loop:
    routes:
      - name: filechain-demo          # 第一條會「動手」的 route
        when: { labels: ['filechain'] }
        profile: filechain
        on_match: create_or_resume    # ← 真接管派工
  inner_loop:
    profiles:
      filechain:                       # main profile
        select:                        # ← 泛化 triage / A-B
          candidates: [filechain_v2]
          method: script
          script: 'uv run select.py'
        agent: { backend: rawcli, ... }
        verify: [ ... ]
        auto_close: off                # 收尾:off=人關、on_success/all=自動關
      filechain_v2:
        agent: { backend: rawcli, model: ... }   # 對照版
        verify: [ ... ]
  ```

## 2. CR → Jira 票(bridge;⚠️ 目前為設計,見 §7)

CR-bridge 在 `agent` 自己的 Jira project 開一張票:

- summary=`登入頁在 Safari 崩潰`、description 帶 CR 摘要
- **貼 label `filechain`**(= 入場券;選哪張入場券 = 選走哪條 route)
- status=**待辦**(To Do)
- 記下 CR id 供未來去重 / 回寫(→ `ticket_session.clearquest_id`,**欄位已預留**)

> 這一步**只建票、單向**;之後**完全由 reactive harness 接手**,bridge 不再碰 Jira
> 工作流狀態(單一寫入者原則)。實作現況見 §7。

此刻兩張 harness 表:**都還沒有這張票的列**(poller 還沒看到它)。

## 3. poller 首輪看到新票 → 建 `ticket_watch`

poller 每輪 `search(jql)` 撈到 SCRUM-42:

1. `store.get(42)` → `None`(沒看過)→ journal **`new_issue`**
2. `match(ticket, routes)` → 命中 `filechain-demo`(票有 `filechain` label)→ journal
   **`route_matched`**(`route=filechain-demo`, `profile=filechain`, `on_match=create_or_resume`)
3. 寫 **`ticket_watch`**:

   | issue_id | key | last_comment_id | last_state | route_name |
   |---|---|---|---|---|
   | 42 | SCRUM-42 | 0 | 待辦 | filechain-demo |

4. `on_match=create_or_resume` → 這票**進 dispatch 候選**;同時**佈建「指令台」**
   (journal **`command_link_posted`**):把指令連結寫進 description 的 control 段 + 貼一則
   指路 comment,人日後由此下 run/retry/hold/stop/cancel/next(綁票、到 close 失效)。
   (若命中的是 `notify_only`/`ignore` → 到此為止:只留 `route_matched`,**不派工、不建
   session、不佈建指令台**——這就是灰度。)

## 4. F1 額度閘 → 首次派工 → triage 選 profile → 鎖進 `ticket_session`

1. **F1 gate**:看 global / per-engine / per-profile 併發還有沒有名額。額滿 → 標
   `queued`(journal `queued`),下輪再試;有名額 → 進 dispatcher。
2. **dispatcher 首次派工**:`store.get_session(42)` → `None`(`sess is None`)。main profile
   `filechain` 有 `select` → 進 **triage**:
   - `select.py` 吃 JSON stdin(ticket/crid/候選+yaml),印出**嚴格 JSON**
     `{"profile":"filechain_v2","reason":"Safari 崩潰,派對照版"}`
   - `filechain_v2` ∈ 候選池 → **選它**;journal **`profile_selected`**
     (`original=filechain`, `chosen=filechain_v2`, `method=script`)
   - (若印 `{"profile":"notfound",...}` → **ABORTED(untriageable)**:profile 寫
     `notfound`、Jira 轉 `cancel_status`;若腳本壞/名字無效 → fail-safe 回 main。見
     [selection](design/selection.md)。)
3. 建 **`ticket_session`**(把選中的 profile **鎖定**,resume 不重選):

   | issue_id | profile | attempts | outcome | session_id | cost_usd |
   |---|---|---|---|---|---|
   | 42 | **filechain_v2** | 0 | None | None | 0.0 |

   此刻推導狀態(§5):有 session、outcome=None、無 pending/queued → **running**。

## 5. provision workspace → 呼叫 claude code → envelope 契約 → 判 outcome

1. **provision**:用 `filechain_v2` 的 template 建 workspace 實例
   `tickets/filechain_v2-42/`,寫 `TICKET.md`(goal / 驗收 / Jira 連結)。journal
   **`session_created`**。
2. **呼叫 claude code**(inner_runner 跑一個 attempt):`claude -p`(或 codex exec),
   帶 `--json-schema` 強制輸出契約 → journal **`attempt_started`** → **`attempt_finished`**
   (`raw`/`cost`/`truly_resumed`)。`ticket_session.attempts` +1、`cost_usd` 累加、
   `session_id` 記下(供 crash 後 native resume)。
3. **envelope 契約** `{reason, status, next, summary, score}`:harness 解析
   `status`→outcome、`next`→換手、`summary`/`score` 進交付物。
4. **grader(可選雙保險)**:profile 的 `verify`(files/cmd/json)跑一遍。
   - 過 → **outcome=SUCCESS** → journal **`resolved`**(帶 `human_minutes_saved`)
   - 不過且還有 attempts → 把**失敗證據**餵回下一輪 resume(證據型停止)
   - `on_unknown` → pending:unknown(等指令台 `run`)

## 6. 貼交付物 → HIL(End) 評分 / auto_close → 關單

1. **終態貼交付物**:讀 workspace 的 `OUTPUT.json` → 小附件(<6MB)貼 Jira、大的走
   `/files/<token>` 一次性下載頁 → ADF comment。journal **`deliverables_posted`**。
2. **收尾**(看 profile `auto_close`):
   - `off`(本例):進 **HIL(End)**,貼**一次性表單連結**(schema `score_and_close`)給人
     評分/裁決。人送出 `close_decision=close` → 系統 `transition(42, "done")` →
     journal **`closed`**(by=human)。
   - `on_success` / `all`:跳過 HIL,人類分數=agent 自評(`score`),自動
     `transition done` → journal `closed`(by=auto)。
3. Jira status 這時才變 **完成(Done)**;票離開 jql(`statusCategory != Done`)→
   下輪 poller 不再撿到 → **概念終點 `closed`**。

最終 `ticket_session`:

| issue_id | profile | attempts | outcome | agent_score | human_score |
|---|---|---|---|---|---|
| 42 | filechain_v2 | 2 | SUCCESS | 8 | 8 |

## 5-bis. 生命週期 6 態怎麼推導(沒有 state 欄)

**DB 沒有 `state` 欄**;dashboard/API 的 6 態由 `canonical_state()` 從
`ticket_session` 的正交原始欄**唯讀推導**(見 [architecture §3.1](design/architecture.md)):

| 推導出的態 | 條件(原始欄) | 本例何時 |
|---|---|---|
| **todo** | 無 session | §2–3(還沒派工) |
| **running** | 有 session、outcome=None、無 pending/queued/inactive | §4–5 |
| **queued** | `queued=true` | §4 若額滿 |
| **hil_middle** | `pending_reason` 有值(need_info/hold/human-decision…)或 inactive | 人在指令台按 `hold` 時 |
| **hil_end** | outcome ∈ SUCCESS/FAILURE/UNKNOWN、尚未關 | §6 等評分 |
| **aborted** | outcome=ABORTED | triage notfound / 指令台 `cancel` |
| **closed** | 人關 Jira(離開 jql) | §6 尾 |

> Jira **status**(待辦/進行中/完成)是給人看的;harness **行為**讀的是
> `ticket_session` 原始欄,不是 Jira status。兩者多數時候一致,但**真相在 session 表**。

## 6-bis. label / status / config 三者關係(一眼表)

| 問題 | 看哪裡 | 說明 |
|---|---|---|
| 這票**會不會被跑**? | label vs `config.routes[].when.labels` | 命中 `create_or_resume` 才跑;`no-agent`→ignore、`agent`→notify_only(不接管) |
| 用**哪個 agent**? | `config.routes[].profile` → 首次派工 triage → `ticket_session.profile` | route 是初選,session 的 profile 才是**最終鎖定** |
| **跑到哪了**? | `ticket_session`(outcome/attempts) | 不是看 label,也不是 Jira status |
| **看過哪些留言/變更**? | `ticket_watch`(last_comment_id/last_state) | 水位去重,以 issue_id 為 key |
| **關單了嗎**? | Jira status=完成 + journal `closed` | 系統只在關單/取消時 `transition()` |

## 7. 實作現況(誠實標註)

| 步驟 | 現況 |
|---|---|
| §3–6 poller/route/gate/triage/dispatch/契約/grader/交付物/HIL/auto_close/關單 | ✅ **已實作** |
| §2 **CR → Jira bridge** | ⚠️ **設計已定、程式未接**:`ticket_session.clearquest_id` 欄位已預留;`scan_cq` bridge 尚未實作。見 [BACKLOG 主題 I](../BACKLOG.md)（I2/I3）與 [architecture](design/architecture.md) D6b 兩段式 |
| §6 尾 **close → CQ 回寫**(把 Jira 連結+結果寫回 CR) | ⚠️ **設計已定、未接 HTTP**:等 CQ base_url + 欄位名;擴充點 `cq_writeback`;所有 close 都該做。見 [BACKLOG 主題 I](../BACKLOG.md)（I1）|

## 8. 出問題怎麼自己 debug(對照每步)

| 症狀 | 先看 | 常見原因 |
|---|---|---|
| 票**完全沒反應** | 有沒有 `route_matched`? | 沒有 = label 沒對到任何 `create_or_resume` route(光有 `agent` label 不夠,那條是 notify_only);或 jql 沒撈到 |
| 命中了但**沒開始跑** | 有 `route_matched` 無 `session_created`? | route 是 `notify_only`/`ignore`(灰度);或 F1 額滿(看 `queued`) |
| **一直卡住** | journal `pending`(讀 `reason`) | 預算超限 / 需人 / 外部變更;指令台 `run` 解 |
| **選錯 agent** | journal `profile_selected`(`chosen`) | select 腳本邏輯;無此事件=用了 route 原 profile |
| 票**被取消** | journal `aborted`(`reason`) | `reason=untriageable`=triage 判不出(profile=notfound) |
| **關不掉** | Jira status + journal `closed` | auto_close=off 在等 HIL 評分;或 `transition` 目標狀態名不符 |

證據在哪、每個事件什麼意思 → [可觀測性](design/observability.md);離線凍結版怎麼查 →
[離線除錯導引](ai-debugging.md)。
