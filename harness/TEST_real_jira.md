# TEST_real_jira — 審批門 / handoff / W4 可視化 真 Jira 實測

> ## ★ W4 全鏈路實測(2026-08-06 08:38-08:42,SCRUM-23/24)— PASS
>
> poller+dashboard 起 → 一輪 poll 內全中,Chrome 肉眼驗證:
>
> | 驗證點 | 結果 |
> |---|---|
> | cron script trigger(`* * * * *` `/bin/sh date`) | ✓ 每分鐘 SUCCESS run,dashboard 列表 profile=`script:clock-demo`,詳情頁 stdout.log 文字檢視 + run.tgz 下載連結 |
> | SCRUM-24 SUCCESS close 打包 | ✓ `transcript_packed [final.html, transcript.tgz]`(63.4KB);ticket 頁 Transcript 卡;**final.html 在 dashboard 內完整渲染**(cclog:session/model/工具/思考計數) |
> | SCRUM-24 KPI | ✓ journal `human_minutes_saved: 15` → 總覽卡「節省人時 0.2h」 |
> | SCRUM-23 handoff 離手定格 | ✓ kind=human 交人(approver accountId)+ `transcript/final.html` 產出 |
> | dashboard 新 UI | ✓ 分頁/status 下拉(filter 後 8 筆)/keyword;新欄位 created/finished 有值;**fetch 局部更新**(SUCCESS 計數 7→8 活跳、無整頁重載、不再 auto-collapse) |
> | graceful shutdown | ✓ `POST /shutdown` → `{"stopping":true}` → 當前輪跑完 →「graceful shutdown(當前輪已完成)」→ rc=0 乾淨退出 |
>
> **符合預期的限制實證**:快照器 `latest.html` 未出現——兩張票都單 attempt
> 數十秒完成,首 attempt sid 未持久化(snapshotter 已註記的限制;根治 =
> sid 預派,W5)。demo trigger 已移除,SCRUM-23/24 轉 Done。

> ## ★ 實測結果(2026-08-05 22:40-22:48,SCRUM-20)— PASS
>
> 完整鏈路一次通過:建票 → 貼 plan(區塊置頂/hash)→ 指派 fox44(email→
> accountId `712020:1b45...`)→ 人在 Jira UI 填表(agent_name + human_email)
> → assignee 交回 bot → 放行 → fork claude haiku(27s,$0.0544)→ verify
> done.txt → SUCCESS comment → 轉 Done。
>
> | 觀察重點 | 結果 |
> |---|---|
> | 1 ADF 往返保真 | ✓ 標記/```/hash 行原樣讀回,hash 重算一致 |
> | 2 人 UI 編輯後機器段 | ✓ **人編輯 human 段後 control 段 hash 仍符**(編輯器沒重排) |
> | 3 approver email→accountId | ✓ assignee=fox44(accountId,非 email 字串) |
> | 4 human_email 校驗 | ✓ 合法 email 放行(ghost 退回未實測,單元測有蓋) |
> | 5 冪等 | ✓ 說明 comment 只 1 則;awaiting 期間 description 未被重寫 |
> | 6 審批中 assignee 開關不誤標 | ✓ 交人事件有進、無 inactive_set |
>
> 未實測(單元測已蓋,留後續):退回迴圈(使用者直接填對放行)、
> ghost email 退回、G1 handoff kind=human 的 human_email 指派。
> workspace 命名實證:`tickets/approval-demo__SCRUM-20__10019/ws/`(W1 §2)。
>
> ## ★ 補測結果(2026-08-05 22:58-23:25,SCRUM-21,Chrome 瀏覽器扮人類)— PASS
>
> 用 claude-in-chrome 在 Jira UI 實走**完整退回迴圈**:
> 1. **空表交回** → 「agent_name 必填」comment + assignee 彈回審批者(revisions=1)✓
> 2. **填 `ghost@nowhere.xx` 交回** → 「human_email 不是合法 Jira 帳號」comment
>    + 彈回(revisions=2)—— **真 Jira user-search 校驗生效** ✓
> 3. **修正 `swchen44@gmail.com` 交回** → 放行 → fork claude haiku → done.txt
>    verify ✓ → SUCCESS($0.0345)→ 轉 Done ✓
>
> 全程 UI 編輯(rich editor)後 control 段 hash 皆未破壞(共 3 次人工編輯往返)。
> G1 handoff kind=human 的 human_email 指派仍僅單元測(待有 handoff 情境的真實任務)。
>
> **UI 操作 lesson(供未來自動化/文件參考)**:Jira description 的 code block 內
> `End`/`shift+End` 是「區塊級」選取(會吃掉整段)——瀏覽器自動化或教人填表時,
> 用「游標點到位、直接打字/退格」的零選取編輯最安全;點欄位外空白會把鍵入誤觸
> 成全域快捷鍵(i=assign to me 等)。
>
> ## ★ 補測結果(2026-08-05 23:38,SCRUM-22)— G1 handoff kind=human PASS
>
> `handoff-demo` profile(rawcli claude haiku + `output_schema: true`):票面任務
> 要求人工簽核 → agent 一次 attempt 即回報 G1 結構化
> `{status: handoff, next: {kind: human, to: manager}, reason: …}` →
> dispatcher:pending:human-decision + **assignee fallback 鏈生效**(description
> 無 human 段 → profile.approver=swchen44@gmail.com → user-search 解析
> accountId → 票指派 fox44);agent 自由文字 next.to=manager 只記 journal 不用於指派 ✓。
>
> **實測揪出的修正**:handoff 交人後,下一輪 poll 的 W12 資源開關把 harness 自己
> 改的 assignee 當外部變更,補了與 handoff comment 矛盾的「inactive…改回 assignee
> 恢復」留言。已修:**已 pending 的 session,inactive/清除只記 journal 不留言**
> (留言只給打斷進行中工作的情境);test_lifecycle 補案例。

> 目的:mock 蓋不到的三件事 —— **ADF description 往返保真**(分區段/hash 行經
> Jira 讀寫不變形)、**email→accountId 解析**(approver + human_email)、
> **人工填表/退回迴圈**(真人操作 Jira UI)。
> profile=`approval-demo`(routes.yaml;rawcli claude haiku,任務=建 done.txt)。

## 前置(已完成,2026-08-05)

- [x] `~/.env` 有 JIRA_BASE_URL / JIRA_EMAIL / JIRA_API_TOKEN(bot=swchen.tw@gmail.com)
- [x] read-only smoke 通過:
  - `myself()` → bot accountId `557058:8473...`
  - `find_account_id('swchen44@gmail.com')` → `712020:1b45...`(人類審批者,可解析)
  - 不存在 email → `None`(退回機制的前提)
- [x] routes.yaml 有 `approval-demo` route(label)+ profile(`approval.required: true`,
  `approver: swchen44@gmail.com`)

## 步驟(順序重要:先起 poller、再建票 —— 否則票被 adopt 掉)

### 1. 起 poller(終端 A)

```bash
cd harness
caffeinate -i python3 run_poller.py 20 15      # 20 分鐘 timebox、15s 間隔
```

確認輸出:`adopted N pre-existing ticket(s)` + `control API on http://127.0.0.1:8787`。
(選配)終端 B 起 dashboard:`python3 detail_server.py runtime_live 8788`
→ 開 http://127.0.0.1:8788/ 看徽章/總覽。

### 2. 建測試票(終端 C,poller 起來「之後」)

```bash
cd harness
python3 - <<'EOF'
from arcp_harness.config import jira_credentials
from arcp_harness.jira_source import JiraCloudSource
src = JiraCloudSource(*jira_credentials())
t = src.create_ticket("SCRUM", "實測審批門+human_email",
                      "在工作目錄建立 done.txt,內容隨意。",
                      labels=["approval-demo"])
print("created:", t.key, t.id)
EOF
```

### 3. 等一輪 poll(≤15s)→ 驗證點 A:貼 plan

Jira 網頁打開該票,應看到:
- description 變成 **ARCP 區塊置頂**:human 段最前(空欄+說明)→ control 段
  (帶 `hash:` 行)→ 原始需求沉到區塊下方,`<!-- /ARCP:sections -->` 收尾
- 一則「填表說明」comment(只此一則 —— 冪等)
- assignee 變成 **Shao-wei Chen(swchen44)**
- poller log:`approval` 事件 decision=awaiting

**ADF 往返檢查**:下一輪 poll(15s 後)log **沒有** `hash 不符` WARNING
= Jira 寫入→讀回沒弄壞機器段。有 WARNING 就是往返失真,把 log 留下。

### 4. 先測「退回」→ 驗證點 B

人在 Jira(哪個帳號登入操作都行,判定看票上 assignee 值):
**不填表**,直接把 assignee 改回 **swchen.tw(bot)**。下一輪 poll:
- comment:「填表有誤 … agent_name 必填」
- assignee 自動退回 swchen44
-(選測)human 段填 `human_email: ghost@nowhere.xx` 再交回
  → comment 應多一條「human_email 不是合法 Jira 帳號」

### 5. 正式填表 → 驗證點 C:放行

編輯 description 的 human 段(只動 value):

```yaml
agent_name: approval_demo
human_email: swchen44@gmail.com
```

assignee 改回 **swchen.tw(bot)**。下一輪 poll:
- log:`審批通過,放行` → provision → fork claude(haiku)
- 數十秒後 comment:`outcome=SUCCESS`(verify done.txt 過)
- 本機檢查:`ls runtime_live/tickets/approval-demo__SCRUM-*__*/ws/done.txt`

### 6. 收尾

- Ctrl-C 停 poller(或等 timebox);票在 Jira 轉 Done(乾淨;SUCCESS session
  本來也會 skip)
- 記錄:總 cost(SUCCESS comment 有)、poller log 全文、有無 WARNING

## 觀察重點(實測要回答的問題)

| # | 問題 | 判定 |
|---|---|---|
| 1 | ADF 往返保真:標記/``` 行/hash 行讀回不變形 | poll log 無 `hash 不符` WARNING |
| 2 | 人在 Jira UI 編輯 human 段後,機器段是否被編輯器改壞 | 同上;若 WARNING=編輯器重排(known:還原需權威版 store,W10 未接,先觀察) |
| 3 | approver email→accountId | 票 assignee 顯示為人(swchen44),不是 email 字串 |
| 4 | human_email 校驗 | ghost email 被退回;合法 email 放行 |
| 5 | 冪等 | 說明 comment 只 1 則;awaiting 期間 description 不被重寫 |
| 6 | 審批中 assignee 開關不誤標 | 交人期間 log 無 inactive_set |

## 疑難排解

- **票被標 adopted / 沒反應** → 建票時 poller 還沒起。開著 poller 重建一張新票。
- **port 8787 被占** → 舊 detail_server(改用 8788 了)或殘留進程:`lsof -i :8787`。
- **假 stall / 沒下一輪** → 筆電睡眠凍結計時器;確認有 `caffeinate`,異常先查
  `pmset -g log | grep -i sleep`。
- **hash WARNING** → ADF 往返失真;留 log + Jira description 原文,回報。
