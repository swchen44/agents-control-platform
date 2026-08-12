# Jira Data Center 相容(主題 L)

> **一句話**:config `source.jira_flavor: dc` 一鍵切換 —— 端點 api/2、PAT/帳密認證、
> `name`(username)識別、`[~username]` mention、wiki 純文字;**Cloud 行為零變**
> (預設 `cloud`)。實作集中 `src/arcp/jira_source.py`(頂部 `_FLAVOR` 差異表)。
> 內網第一次上線照 [DC 首驗 checklist](../dc-first-run-checklist.md) 逐項勾。

## 為什麼

開發/家用環境是 Atlassian **Cloud**(swchen44.atlassian.net);**內網生產是
Data Center**。兩者 REST 有系統性差異——最要命的是 DC **沒有 accountId**
(識別碼是 `name`/username),`[~accountid:x]` 在 DC 是**安靜失敗**:comment
建得出來、文字看得到,但**不觸發通知**。故差異必須顯式建模,不能靠「大致相容」。

## 差異表(單一真相 = `jira_source._FLAVOR`)

| 差異面 | cloud | dc |
|---|---|---|
| REST 版本 | `/rest/api/3` | `/rest/api/2` |
| 搜尋 | `/search/jql`(nextPageToken;404/410 退舊端點) | `/search`(startAt/total;每輪取一頁,與 Cloud 對稱) |
| 認證 | `JIRA_EMAIL`+`JIRA_API_TOKEN`(Basic) | **`JIRA_PAT`(Bearer,8.14+,建議)** 或 `JIRA_USERNAME`+`JIRA_PASSWORD`(Basic) |
| 使用者識別 | `accountId` | `name`(username) |
| user search | `?query=email` → accountId | `?username=` → name(同時比對 username/顯示名/email) |
| @mention | `[~accountid:x]` | `[~username]` |
| assign body | `{"accountId": x}` | `{"name": x}` |
| watcher body | 裸 accountId 字串 | 裸 username 字串(同形) |
| description/comment | ADF doc(`text_to_adf` 往返) | **純文字/wiki 字串**(api/2 直接吃;讀取 `adf_to_text` 對字串 passthrough) |
| 結構化交付物 | `adf.py` blocks(`add_comment_adf`) | `deliverables.build_comment_wiki`(`h3.`/`h4.`/`[label\|url]`/`*` 清單)走 `add_comment` |

認證判定約定:`config.jira_credentials(flavor="dc")` 回 `(base_url, user, secret)`,
**PAT 時 user 為空字串** → source 端「dc 且 user 空」= `Authorization: Bearer`。

## email → 識別碼解析(L6/L7)

`identity.resolve_user_id(email, source, store, user_map, username_rule)` 查序:

1. **config `source.user_map`**(手動映射)——DC user search 需「Browse users」
   全域權限,**內網帳號可能被擋**;map 是逃生路,也可 hot reload。
2. **store `user_dir` 快取表**(SQLite)——查過的不再反查(L7)。
3. **`source.find_user_id`**(user search;命中**寫回快取**)。
4. **`source.username_rule`** 推導——`local`(email @ 前段)或含 `{local}`
   的模板(如 `corp-{local}`);適用公司 username 規則固定的環境。

用途邊界:mention / approver watcher / set_email re-tag 走此查序;
**approval 的 human_email 驗證仍走 source 直查**——rule 推導值不能當
「合法 Jira 帳號」的證據。

### Cloud user search 三坑(已知,查序同樣兜住)

- 呼叫者沒「Browse users」權限 → **回空陣列**(不是 403,安靜失敗)。
- `emailAddress` 受 GDPR 隱私設定,常為空 → 精確比對失敗;唯一命中則取之。
- 個人 email(gmail 等)可能查不到 → user_map / rule 兜底。

## 設定

```yaml
outer_loop:
  source:
    jira_flavor: dc               # 預設 cloud;Cloud 部署不用動任何東西
    # user_map:
    #   alice@corp.com: alice     # cloud 填 accountId、dc 填 username
    # username_rule: local        # 或 'corp-{local}' 模板
```

`~/.env`(dc):`JIRA_BASE_URL=https://jira.corp.example` + `JIRA_PAT=...`
(或 `JIRA_USERNAME=`+`JIRA_PASSWORD=`)。

## 驗證策略

家用環境無 DC 可打 → **mock 單元測**(`tests/test_jira_dc.py`:差異表/auth
三模式/credentials 分支/search 端點/識別欄位/mention/wiki/resolve 查序)入 CI;
**真 DC 的最終確認在內網首次上線時做**,照
[dc-first-run-checklist.md](../dc-first-run-checklist.md) 逐項勾(mention 是否
真的觸發通知、watcher、transition、附件……)。

## 實作對照

| 關注點 | 位置 |
|---|---|
| 差異表 / 端點 / auth / wiki body | `jira_source.py`(`_FLAVOR`、`_rich`、`search`、`mention_tag`、`my_uid`) |
| credentials 分支 | `config.jira_credentials`(flavor 參數) |
| mention 收斂 | `jira_source.mention_tag_of` ← hil / scoring / commands |
| 識別解析查序 | `identity.resolve_user_id`;快取 `store.user_dir`(get/put_user_uid) |
| 交付物 wiki 版 | `deliverables.build_comment_wiki` + `post_deliverables` flavor 選路 |
| 接線 | `run_poller`(jira_flavor / user_map / username_rule,皆可 reload) |
