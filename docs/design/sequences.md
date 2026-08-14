# Sequence Charts — 開票與全部 HIL 場景(角色統一)

> 所有圖用**同一組角色**(Mermaid;GitHub/內網支援 mermaid 的 viewer 直接渲染,
> 純文字也可讀)。事件名 = journal 事件([observability](observability.md)),
> 可直接對照 dashboard 時間軸。Block diagram 見 [architecture](architecture.md),
> 狀態機見 web `/concepts`。

**角色(全篇一致)**:

| 角色 | 說明 |
|---|---|
| `H` 人 | 負責人/管理者(開票、填表單、下指令) |
| `CQ` WITS/CQ | 外部 issue 系統(CR 編號) |
| `J` Jira | 票=人機溝通紀錄(狀態同步/留言/附件) |
| `P` ARCP | poller+dispatcher(輪詢、路由、派工、驗收、回寫) |
| `F` 表單 | 一次性 token 表單服務(HIL 人機介面) |
| `A` Agent | headless CLI(`claude -p` / `codex exec`,只讀 workspace) |

---

## 1. 主流程:scan 開票 → route/triage → 派工 → 驗收 → 等評分

```mermaid
sequenceDiagram
  participant CQ as CQ(WITS)
  participant H as 人
  participant J as Jira
  participant P as ARCP
  participant A as Agent

  rect rgb(240,240,240)
  note over CQ,P: 來源A:scan job(trigger cron/count)
  P->>CQ: scan script 掃 CR
  P->>P: CRID 去重(store+REST 預濾)
  P->>J: create_ticket(labels=arcp.*、crid:/email:/prompt:)
  end
  note over H,J: 來源B:人直接開票(labels=入場券)

  loop 每 poll_interval_sec
    P->>J: jql 輪詢
  end
  J-->>P: new_issue
  P->>P: route 比對(labels/keyword AND,先到先贏)
  opt profile 有 select(triage)
    P->>P: random 分流 / script 判類(回 notfound→中止轉取消)
  end
  P->>P: workspace 佈建(template+skills+TICKET.md 插值{crid})
  P->>P: 安全掃描 TICKET.md(命中→場景3)
  P->>J: 狀態同步 In Progress + TICKET.md 版本附件
  P->>A: spawn(claude -p / codex exec)
  A->>A: 讀 TICKET.md → 做任務 → 產出+OUTPUT.json
  A-->>P: envelope(completed/session_id/cost)
  P->>P: verify(grader 證據:files/cmd/json)
  alt verify 過
    P->>J: resolved(SUCCESS)+ 交付物 comment/附件 → Resolve(等評分,場景6)
  else verify 不過(< max_attempts)
    P->>A: 帶失敗證據 native resume 重試
  else 次數用盡
    P->>J: FAILURE → Resolve(等人裁決)
  end
```

## 2. HIL:審批門(approval;`require_approval`)

```mermaid
sequenceDiagram
  participant H as 人(approver)
  participant J as Jira
  participant P as ARCP
  participant F as 表單
  participant A as Agent

  P->>J: 貼執行計畫(plan)+ 狀態 Pending
  P->>F: 建 approval 一次性表單
  P->>J: @mention approver + 表單連結(assign approver)
  H->>F: 開連結,看 plan,填 agent 名
  F->>P: 提交 = 放行(單一信號)
  P->>J: assign 收回 bot、journal approval(proceed)
  P->>A: 開始派工(進場景 1 的 spawn)
  note over P,F: 表單遺失 → 下輪 poll 自癒補發
```

## 3. HIL:安全審(security;TICKET.md 掃描命中,fail-closed)

```mermaid
sequenceDiagram
  participant H as 人
  participant J as Jira
  participant P as ARCP
  participant F as 表單
  participant A as Agent

  P->>P: spawn 前掃 TICKET.md → 命中(或掃描器故障)
  P->>J: pending:security → Pending(不 spawn)
  P->>F: 建 security_review 表單(原文+命中理由+可修文字框)
  P->>J: @mention + 連結
  H->>F: 裁決
  alt continue(修訂文字)
    F->>P: 修訂版存 sidecar + sec_reviewed_at
    P->>A: spawn(TICKET.md 描述段=修訂版,標註人工安全審)
  else abort
    F->>P: ABORTED(reason=security)
    P->>J: 轉 Cancelled + 結案存證(場景8)
  end
```

## 4. HIL:budget 增額(單票 soft 上限破)

```mermaid
sequenceDiagram
  participant H as 人(負責人)
  participant J as Jira
  participant P as ARCP
  participant F as 表單
  participant A as Agent

  P->>P: attempt 前 precheck:tokens/usd ≥ soft
  P->>J: pending:budget → Pending
  P->>F: 建 budget_increase 表單(已用量/soft/hard+進度)
  P->>J: @mention + 連結
  H->>F: 自助調高 soft(≤hard,超過 hard 要管理者改 profile)
  F->>P: 提交 → 清 pending
  P->>A: 下輪 native resume 續跑(attempt 不重來)
```

## 5. HIL:hold 中斷改方向(人主動)

```mermaid
sequenceDiagram
  participant H as 人
  participant J as Jira
  participant P as ARCP
  participant F as 表單
  participant A as Agent

  H->>P: 指令台 hold(REST/表單)
  P->>A: EVICT 檔 → watchdog killpg(不耗 attempt)
  P->>J: pending:hold → Pending
  P->>F: 建 hold 表單
  H->>F: 填新指示
  F->>P: 提交:指示→TICKET.md 人類指示段 + resume note、清 pending+EVICT
  P->>A: native resume(prompt 顯式帶「人類最新指示」)
  A->>A: 帶著新指示續做 → 完成
  note over P,A: 併發防護:spawn 前兩道 fresh-read 閘+evicted 分支欄位合併(lesson 18)
```

## 6. HIL(End):評分與三種裁決(close / continue / handoff)

```mermaid
sequenceDiagram
  participant H as 人
  participant J as Jira
  participant P as ARCP
  participant F as 表單
  participant A as Agent

  P->>J: 終態(SUCCESS/FAILURE)→ Resolve
  P->>F: 建 score_and_close 表單(三訊號:grader+agent 自評+交付物駕駛艙)
  P->>J: @mention + 連結(逾時週期催辦)
  H->>F: 評分 0–10(必填)+ 裁決
  alt close(關單)
    F->>P: human_score 入庫
    P->>J: 精確轉 Closed(status_sync 兩步保險)+ 結案存證(場景8)
  else continue(打回續作,可附指示)
    F->>P: 解終態+attempts 歸零、指示→TICKET.md+resume note
    P->>A: resume 續做 → 回到終態再評
  else handoff next(同票換手)
    F->>P: reset session、鎖定新 profile、重佈建
    P->>A: 新 profile 在同票重新開始(脈絡=同一張票)
  else handoff base(跨票交接)
    P->>J: create_ticket 開新票(base_ref=本票)
    P->>P: 新票首佈建注入 BASE_<key>/(TICKET+envelope+HANDOFF)
    P->>J: 本票 ABORTED(交接,不算失敗)→ Cancelled
  end
```

## 7. auto_close(無人值守;`auto_close: on_success|all`)

```mermaid
sequenceDiagram
  participant J as Jira
  participant P as ARCP
  participant A as Agent

  A-->>P: 終態 SUCCESS(envelope 含自評 score)
  P->>P: human_score = agent 自評(取不到=None,不擋)
  P->>J: 精確轉 Closed(同人評 close 的兩步保險)
  P->>J: 結案存證(場景8)+ auto_close comment(by=auto)
  note over J,P: 不發評分表單,全程無人
```

## 8. 收尾共通:結案存證 + 例外(unknown / external)

```mermaid
sequenceDiagram
  participant H as 人
  participant J as Jira
  participant P as ARCP
  participant A as Agent

  note over J,P: 任何 close/cancel/abort 匯聚點(Q 波)
  P->>J: description 置頂 [ARCP owner=result](完成度/評分/花費/時長/crid)
  P->>J: 附件:timeline.jsonl + SESSION.md + transcript.html(>6MB 留路徑)

  rect rgb(240,240,240)
  note over H,A: 例外A:UNKNOWN(行程消失,無法證明副作用)
  A--xP: 無 envelope
  P->>J: pending:unknown → Pending(不自動重試,v5 D3)
  H->>P: 查 transcript 後指令台 run/retry/cancel
  end
  rect rgb(240,240,240)
  note over H,A: 例外B:external(infra 故障)
  P->>J: pending:external(不耗 attempt)
  P->>A: server 修復後下輪自動 resume(人不用動)
  end
```

## 9. rerun:資訊更新後同票乾淨重跑(含 ABORTED 復活)

```mermaid
sequenceDiagram
  participant H as 人
  participant CQ as CQ(WITS)
  participant J as Jira
  participant P as ARCP
  participant A as Agent

  note over H,J: 票已停在 ABORTED / FAILURE / 等評分,結果不對
  CQ-->>H: (可選)CR 上有更新的資訊
  H->>J: 更新 description(把新資訊/正確參數寫進去)
  H->>P: 指令台 rerun(需確認,可選填補充指示)
  P->>P: reset session(忘掉舊對話)+ 刪舊工作區
  opt 有補充指示
    P->>J: 指示寫入 description human 段
  end
  P->>P: 重佈建 + 重渲染 TICKET.md(新描述、{crid} 插值)
  P->>A: 同 profile 全新 spawn
  A->>A: 帶更新後的資訊從頭做
  A-->>P: 完成 → verify → 回到正常流程(場景 1/6)
```

與相鄰路徑分工:`retry`=同 session resume(帶舊對話)、HIL(End)
`continue`=打回+指示(帶舊對話)、`next`=換 profile、
**`rerun`=不換人但砍掉重練(ABORTED 唯一復活路)**。

---

**對照**:HIL 全表(reason×處理)= [interaction §3.2](interaction.md);
狀態推導 = [architecture §3](architecture.md);TICKET.md 資訊流 =
[workspace](workspace.md);結案存證 = [provenance](provenance.md)。
