# DESIGN_hotreload — Hot Reload 範圍 / 優雅關閉 / 強制關閉(W13/W4.5)

> 使用者 2026-08-06 要求專門文件。核心立場:**reload = 引用替換(swap),
> 不是交易**;**graceful shutdown = 讓當前輪自然跑完**(可接受「壓縮/打包中
> 要等一下」);**強制關閉 = 靠冪等與三態語意兜底**(DESIGN_idempotency),
> 不靠關閉時的清理。

## 1. 機制總覽

```
POST /reload    重讀 config.yaml → swap 引用(make_reload,run_poller.py)
POST /pause     暫停派工(watch/指令照常;正在跑的不中斷)——「軟暫停」
POST /resume    恢復派工
POST /shutdown  graceful:當前 poll 輪(含 attempt/驗證/壓縮打包)自然跑完
                → 主迴圈退出 → snapshotter/control API/store 依序清理
Ctrl-C / SIGTERM  半強制:當輪中斷(見 §4)
kill -9           強制:立即死(見 §4)
```

- 壞 config:`load_config`/`load_profiles`/`load_triggers` 擲 `ConfigError`
  → control API 回 **400、舊設定原封續用**(fail-safe;poller 不死)。
- reload 是**引用替換**:正在跑的那一輪 dispatch 仍讀舊值,**下一輪**才用新
  設定。沒有「半新半舊」的持久狀態——設定不落 store,只活在進程記憶體。

## 2. Hot Reload 範圍(✅ 可 / ⚠️ 部分 / ❌ 不可)

| 項目 | 狀態 | 說明 |
|---|---|---|
| routes(路由規則) | ✅ | 下輪 poll 生效 |
| jql | ✅ | 下輪 poll 生效 |
| concurrency(F1 三層額度) | ✅ | 下輪 gate 生效 |
| profiles(inner_loop 全部欄位:agent/verify/approval/retention/KPI…) | ✅ | dispatcher / external policy / 指令台(profiles_fn getter)同步 swap |
| triggers(scheduled/oneshot/script) | ✅(W4.5 補) | 原漏項;新 trigger 下輪檢 due |
| ~~commands.allowed_commenters~~ | 🗑️ 已移除 | 人的指令改走「指令台」表單/REST,無 @agent 留言白名單 |
| external_change.cancel_states | ✅(W4.5 接線) | 改完 reload 生效 |
| **進行中的 attempt** | ⚠️ | 用舊 profile 跑到本 attempt 結束;下一 attempt(同輪 while 迴圈內)即用新 profile 的 verify/budget——**同輪內混用是接受的限制** |
| session 鎖定的 profile 名 | ⚠️ | reload 換的是 profile **內容**;鎖定(F3 換手)指的 profile **名字**不變。改名 profile → 鎖定失效退回 route 推導(dispatcher 查無此名) |
| pending/queued 票 | ✅ | 不受影響;下次評估用新設定 |
| snapshot_interval_sec | ❌ | Snapshotter 啟動時定;改間隔要重啟(快照器輕量,可接受) |
| control host/port | ❌ | HTTP server 已綁定 |
| bot_account_id | ❌ | 啟動時 myself() 解析一次;換 bot 帳號要重啟 |
| Jira 憑證(~/.env)/ write_retry | ❌ | JiraCloudSource 建構時定 |
| store 目錄(runtime_live) | ❌ | DB 連線已開 |
| dashboard(detail_server) | ❌(不需要) | 獨立進程、無狀態,直接重啟它即可,不影響 poller |
| **程式碼本身** | ❌ | reload 只重讀 config,不 reimport;改 code = graceful shutdown → 重啟(store 持久 + resume,見 §5) |

## 3. 優雅關閉(POST /shutdown)

語意:**「不再開始新的一輪;正在跑的這一輪讓它自然跑完」**。

- 當前輪包含:attempt(agent 子進程跑完)、grader 驗證、Jira comment、
  transcript finalize、**tgz 壓縮打包**——全部做完才退。使用者已確認接受
  「壓縮中要等一下」。
- 退出後依序:snapshotter.stop()(join)→ control API stop → store.close()。
- `stopping` 也會反映在 `GET /status`,dashboard 之後可顯示(UI 依使用者
  指示與 new dashboard 一起做,本波不動)。
- 想「先軟後硬」:先 `/pause`(不派新工、觀察 in-flight 歸零)再 `/shutdown`
  ——等待窗最短、最可控。

## 4. 強制關閉(語意與後果)

| 方式 | 行為 | 後果與兜底 |
|---|---|---|
| Ctrl-C / SIGTERM | 主迴圈 KeyboardInterrupt → break → 仍走清理;但**當輪中斷**:attempt 子進程可能被中斷 | 無 envelope → 下次啟動該 attempt 判 **UNKNOWN / 重派**(inner runner envelope 驅動,exit code 不作數);Jira 寫入靠「先持久化再外寫」at-most-once(DESIGN_idempotency #3/#4)不重複 |
| kill -9 | 立即死,無清理 | 同上 + 半成品防護:provision tmp+rename 原子(#7)、tgz 打包中斷留半檔 → 下次 close 重打包覆蓋;store WAL 交易安全;watch watermark 防 comment 重放 |
| 電源斷/睡眠凍結 | 同 kill -9 | 同上;睡眠假 stall 另見 memory(caffeinate) |

原則:**強制關閉的安全性不是靠關閉時做對事,而是靠重啟後的恢復語意**
(evidence-based:envelope 缺 = 不能證明 = UNKNOWN;冪等表 9 條路徑見
DESIGN_idempotency)。已知殘餘風險 = 盤點 #5(attempt 中途 harness 死 →
重跑該 attempt 重花錢;根治 = sid 預派,W5 候選)。

## 5. 重啟恢復(shutdown/升級後)

1. store(harness.db WAL)+ journal(events.jsonl)持久,重啟全在。
2. `adopt_existing`:只認**新票**;既有票已在 watch 水位內,不重播歷史。
3. 終態/pending/inactive session 原樣;active 但無 envelope 的 attempt →
   三態判定(UNKNOWN 交人 / 或 attempts 未消耗則重派)。
4. native resume:session_id 在 store,`--resume`/`codex exec resume` 續跑
   不重工(A2 agent 層)。

## 6. 已知限制(接受並記錄)

- 同一輪 while 內 profile 新舊混用(§2 ⚠️)。
- shutdown 等待窗 = 最長一輪(attempt timeout 300s + 驗證 + 打包);急停用
  Ctrl-C 換取 UNKNOWN 風險。
- reload 不含程式碼;snapshot 間隔/port/bot 身份要重啟。
- `/shutdown` 若在 sleep(interval)間下達,最多再等一個 interval 才退出。
