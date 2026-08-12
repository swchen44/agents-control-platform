# Jira Data Center 內網首驗 checklist(主題 L)

> 家用開發環境只有 Cloud;DC 路徑已由 mock 單元測覆蓋,但**真 DC 的行為確認
> 只能在內網做**。第一次上線照此逐項勾;任何一項不符 → 記 log +
> 對照 [design/jira-dc.md](design/jira-dc.md) 的差異表排查。

## 0. 前置

- [ ] 確認 Jira 版本 ≥ 8.14(個人頭像 → Profile → 版本資訊;或問管理員)。
- [ ] 建 PAT:頭像 → **Profile → Personal Access Tokens → Create token**;
      存進 `~/.env`:`JIRA_PAT=...`。無 PAT 選項(版本太舊)→ 改
      `JIRA_USERNAME=` + `JIRA_PASSWORD=`。
- [ ] `~/.env` 填 `JIRA_BASE_URL=https://<內網 jira>`(或 config
      `source.jira_base_url`)。
- [ ] `config/config.yaml`:`source.jira_flavor: dc` + 專案 `project` / `jql`。

## 1. 連線 / 認證

- [ ] `uv run python scripts/smoke_jira.py` —— `myself()` 回你的帳號
      (顯示 `name`=username,**不是** accountId)。401 → PAT 失效/沒帶;
      403 → 帳號權限。

## 2. 讀路徑

- [ ] poller 跑一輪能撈到票(`search` 走 `/rest/api/2/search`)。
- [ ] 票的 description / comment 讀出來是**純文字**(不是 JSON 殘渣)。
- [ ] `assignee_id` 欄位是 username(dashboard DB Browser 看 ticket_watch)。

## 3. 寫路徑(開一張測試票驗)

- [ ] `add_comment`:留言出現且格式正常。
- [ ] **@mention 真的觸發通知**(最關鍵——`[~username]` 打錯語法時
      文字看得到但**不會通知**):請同事確認有收到 Jira 通知/信。
- [ ] `assign`:改 assignee 成功(body 用 `name`)。
- [ ] `add_watcher`:watcher 名單出現該人(profile.approver 開票自動加)。
- [ ] `set_description`:description 更新且**區段 hash 機制正常**
      (下一輪 poll 不誤判外部變更)。
- [ ] `transition`:轉狀態成功(名稱在地化無妨,走 statusCategory)。
- [ ] `add_attachment`:附件上傳成功(multipart + `X-Atlassian-Token`)。

## 4. user search / 識別解析

- [ ] `find_user_id("<同事 email>")` 回 username(python -c 一行測)。
      回 None → 大概率是帳號沒「Browse users」權限:改 config
      `source.user_map` 手動映射,或設 `username_rule`(公司規則固定時)。
- [ ] set_email 改負責人 → 新負責人被 @mention **且收到通知**。

## 5. 端到端

- [ ] 一張真票走完:route → 佈建 → agent 跑 → 回寫 comment → HIL 表單
      連結可開 → 評分 → close;dashboard `/timeline` 與 ticket 頁正常。
- [ ] 交付物 comment(有 OUTPUT.json 的票)以 **wiki 格式**呈現
      (`h3.`/`h4.` 標題有渲染、`[label|url]` 連結可點)。

## 常見症狀速查

| 症狀 | 原因 | 解 |
|---|---|---|
| mention 文字在、沒通知 | 語法/username 錯 | 確認 `[~username]`;user_map 校正 |
| user search 一律空 | 無 Browse users 權限 | `user_map` 手動映射 / `username_rule` |
| 401 | PAT 過期/沒帶 | 重建 PAT |
| comment 變成一坨 JSON | flavor 沒切到 dc(還在送 ADF) | config `jira_flavor: dc` |
| search 400 | JQL 欄位名 DC 不支援 | 調 `source.jql` |
