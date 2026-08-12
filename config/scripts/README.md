# config/scripts/ — config 引用腳本的家(J1 + M1)

**config 裡所有腳本**——`outer_loop.triggers[].script` **和 profile 的
`select.script`**——都相對這裡,且**必須放子資料夾**:`{subfolder}/xxx.sh`
(直接放本資料夾根會被拒;路徑穿越 `../` 亦擋)。執行時 **cwd 進該 subfolder**
(腳本能讀自己旁邊的檔)。統一解析:`arcp.paths.resolve_config_script`。

- trigger script:log 存 `runs/<name>__<run_name>__<ts>/transcript/`
  (stdout/stderr.log + run.tgz;dashboard 可看可下載)。
- select script:JSON stdin(ticket/crid/候選+yaml)→ stdout 回
  `{"profile": "...", "reason": "..."}`(見 docs/design/selection.md);
  要用 uv/npx 等 runner 請寫進腳本 shebang 或包一層 .sh。

兩種 `trigger_type`:

- **script-job**:純做事,stdout 只是 log,不開票。
- **agent-job**:stdout **必須是 JSON 任務清單** → 每筆**像人一樣**開一張 Jira 票
  (不建 session、不鎖定 profile)→ 票走 route/triage。格式:

  ```json
  [{"summary": "...", "description": "...", "labels": ["arcp.cr"], "crid": "WCNCR0123745"}]
  ```

  - `summary` / `description` 必要(至少 description)。
  - `labels`(**兩層:預設 vs 覆寫**):job 的 `labels`(config,agent-job **必填**)是每張票的
    **保底入場券**——確保開出來的票一定命中某條 route、不會變沒人撿的孤兒票。單筆任務**省略**
    `labels` 就用這個保底;只有想把同一個 job 的不同任務**分流到不同 route** 時,才在該筆回自己
    的 `labels` **覆寫**(例:緊急筆回 `["arcp.urgent"]`、一般筆省略走保底)。程式:
    `it.get("labels") or trigger.labels`(`triggers.py`)。
  - `crid`(選填):來源 ClearQuest CR id → 寫進票 description 最上面的 yaml
    (`crid: …`)→ dispatcher 建 session 時讀回 `session.clearquest_id`。
    **CRID 去重(兩層)**:harness 開票前**必擋**(查 session.clearquest_id +
    watch description;同 CRID 已有票 → journal `job_skip_duplicate`、不開);
    script 可另用 REST 預濾(`GET /api/v1/tickets/<CRID>`,200=已有、404=新)
    省日誌雜訊——範例見 `cq/scan-example.sh`。

範例見 `example/scan.sh`。腳本請視自己的資料夾為唯讀(輸出走 stdout,別在 config/ 下寫檔)。
