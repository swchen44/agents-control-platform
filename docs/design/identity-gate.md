# 負責人 email 身分門禁(主題 K)

> **一句話**:description 契約的 `email` 首建時存進 `session.owner_email` 當「這張票的
> 負責人」;之後 HIL 表單 / 指令台提交**必填 email 且要通過比對**才放行。這是在
> 「知道 capability URL」之外再加一層「你得是本人」的門禁——防連結被轉發給非授權者。
> 實作:`src/arcp/identity.py`(`owner_gate`)、`form_server`、`commands`、`dispatcher`。

## 為什麼

一次性 / 綁票的 capability URL 本身就是一種門禁(**知道連結才進得來**)。但連結可能被轉發、
被截、貼到群組。email 門禁是**選填的第二層**:填了 email 的票,只有「本人 / 管理者 / 審批者」
的 email 能真正提交,即使別人拿到連結也擋得下,並留 IP 稽核。

## owner_email:來源、鎖定、更新(K1 + K3)

- **來源**:`description` 頂端 yaml 契約的 `email`(J2 契約,見 [triggers 契約](../walkthrough-cr-to-agent.md))。
  dispatcher **首建 session** 時 `parse_ticket_meta(...).get("email")` → 正規化存進
  `session.owner_email`(比照 `crid`)。
- **鎖定**:首建後**鎖定**,`description` 後續改**不同步**(單一真相源、不被 poll 覆回)。
- **更新**:只認指令台 `set_email` 指令改(見下)。改了會 **re-tag 新負責人 + 重發待填表單**。

## 門禁規則(K1)

`identity.owner_gate(submitted, session, profile, admin_emails) -> (放行?, 訊息)`:

| 條件 | 結果 |
|---|---|
| `owner_email` **為空** | **放行**(選填門禁未啟用;此票沒上鎖) |
| `submitted == owner_email` | 放行(負責人本人) |
| `submitted ∈ admin_emails` | 放行(全站管理者豁免,config `outer_loop.admin_emails`) |
| `submitted == profile.approver` | 放行(該票 profile 的審批者) |
| 以上皆非 | **擋下**(回拒絕訊息) |

- **正規化**:比對前一律 `strip + lowercase`(email 慣例大小寫不敏感)。
- **選填(opt-in)**:只有 description 填了 email 的票才啟用比對;沒填的票維持現狀
  (表單仍要「有填 email」供稽核,但不比對)。這樣不卡現有 / 沒填 email 的流程。

## 比對範圍(K1)

**HIL 表單 + 指令台都比對**(`form_server._gate`):

- **HIL 表單**(補資訊 / 決策 / 評分):`form_server` 加了 email 必填欄(`name=by`);
  `do_POST` 先驗非空 + `owner_gate`,過了才 `process_submission`。
- **指令台**(run/retry/hold/stop/cancel/next/set_email):`_command_submit` 在驗 email
  非空後加 `owner_gate`。

## 稽核(K2)

「上傳表單的人都要留 log 供追查」:

- **HIL 表單**:`interactions` 表存 `submitted_by`(email)、`submitted_ip`(來源 IP)、
  `submission`(提交的資料)。IP 由 `form_server` 的 `client_address` 取得。
- **指令台 / REST 指令**:`apply_command` 的 journal(`command_accepted` / `handoff` /
  `owner_changed`)帶 `author`(email)+ `ip`;稽核 comment 也附 ip。REST(`control_api`
  `/ticket/<id>/command`)由 `handler.client_address` 取 IP。

## `set_email`:改負責人(K3)

指令台指令,改 `session.owner_email`。**門禁閉環**:`set_email` 經 `apply_command`,而呼叫端
(`form_server._gate`)用**當前** owner_email 比對,所以**只有現負責人 / 管理者 / 審批者**能改
負責人(否則任何有連結的人都能換人 = 門禁失效)。列為破壞性指令(二次確認)。

改了(新 ≠ 舊)的副作用:

- **re-tag 新負責人**:`find_account_id(新 email)` → 查得到 `@mention` accountId、查不到退
  純文字 email 留言(Jira @mention 用 accountId 不是 email)。
- **重發待填表單**:`open_interactions_for_ticket` 有待填 HIL 表單 → 重貼連結給新負責人;
  沒有則純 re-tag 通知。
- journal `owner_changed`(old/new/author/ip/retagged/reissued)。

## approver watcher(K4)

**首建 session(鎖定 profile)時**把該 `profile.approver` 加為 Jira **watcher**(關注者),
讓審批者收到該票的 Jira 通知。`dispatcher._add_approver_watcher`:approver 為 email 先
`find_account_id` 轉 accountId、已是 accountId 直接用;`jira_source.add_watcher` 打
`POST /issue/<key>/watchers`。best-effort(查不到 / 失敗不擋派工);只首建加、resume 不重加。
涵蓋 approval 分支 + 一般分支,agent-job 走完 triage 定 profile 後也在此加。

## 設定(config)

```yaml
outer_loop:
  admin_emails:              # K:全站管理者 email(門禁豁免;可 hot reload)
    - ops@company.com
    - lead@company.com
```

## 實作對照

| 關注點 | 位置 |
|---|---|
| 門禁純函式 | `identity.owner_gate` / `normalize_email` |
| owner_email 欄 | `store.TicketSession.owner_email`(+ migration) |
| 首建存入 + approver watcher | `dispatcher`(approval / 一般兩處建 session) |
| HIL email 欄 + 兩表單比對 | `form_server`(`render_form_page` / `_gate` / `do_POST` / `_command_submit`) |
| 稽核 IP | `interactions.submitted_ip`;`apply_command` journal `ip` |
| set_email 指令 | `commands.apply_command`(cmd == `set_email`) |
| add_watcher | `jira_source.add_watcher` |
| admin_emails 載入 | `run_poller`(建構 + reload)→ `dispatcher.admin_emails` → `form_server.admin_emails_fn` |
