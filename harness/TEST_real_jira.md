# TEST_real_jira — W2a 審批門 + human_email 真 Jira 實測

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
