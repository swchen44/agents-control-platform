# config/scripts/ — job 腳本的家(J1)

`outer_loop.triggers[].script` 相對這裡,放在**子資料夾**下:`{subfolder}/xxx.sh`。
執行時 **cwd 進該 subfolder**(所以腳本能讀自己旁邊的檔),log 存
`runs/<name>__<run_name>__<ts>/transcript/`(stdout/stderr.log + run.tgz;dashboard 可看可下載)。

兩種 `trigger_type`:

- **script-job**:純做事,stdout 只是 log,不開票。
- **agent-job**:stdout **必須是 JSON 任務清單** → 每筆**像人一樣**開一張 Jira 票
  (不建 session、不鎖定 profile)→ 票走 route/triage。格式:

  ```json
  [{"summary": "...", "description": "...", "labels": ["cr"], "crid": "WCNCR0123745"}]
  ```

  - `summary` / `description` 必要(至少 description);`labels` 省略則用 job 的 `labels`。
  - `crid`(選填):來源 ClearQuest CR id → 寫進票 description 最上面的 yaml
    (`crid: …`)→ dispatcher 建 session 時讀回 `session.clearquest_id`(去重 + close→CQ 回寫)。

範例見 `example/scan.sh`。腳本請視自己的資料夾為唯讀(輸出走 stdout,別在 config/ 下寫檔)。
