# config/profiles/ — 拆檔的 agent profile(選用)

profile 可以**內建在 `config/config.yaml` 的 `inner_loop.profiles`**,也可以**一檔一個**
放在這裡,讓命名精準、分檔 owner、好管理(Q15)。

- **檔名 = profile 名**(如 `filechain.yaml` → profile `filechain`);內容 = 該 profile 的
  body(就是原本會放在 `inner_loop.profiles.<名>` 底下的那塊)。
- `load_profiles` 會自動合併「主檔 inline profiles」+「本資料夾所有 `*.yaml`」。
  **同名跨檔衝突 → 啟動即報錯**(fail-fast)。非 `.yaml`(如本 README)略過。
- 每個 profile 記得自己的來源檔(`source_yaml`),供 Q16 `select` 的 script 拿到候選者的
  yaml 絕對路徑。

範例(`config/profiles/filechain.yaml`):

```yaml
goal: '完成 ticket 描述交付的任務並通過驗證'
workspace: { template: empty, folder: 'tickets/{issue_id}' }
agent: { backend: rawcli, engine: claude, model: haiku }
verify:
  - name: task-done
    files: { DONE.md: }
loop: { max_attempts: 3, on_unknown: pending }
```

> `config.example.yaml` 為求 CI 自足,profile 仍內建在主檔;要拆檔管理時把它們搬進這裡即可。
