# PLAN_wave5 — 韌性收尾 + 後續候選

> 承 W4(M15/M16 + dashboard v2 + hot reload 文件)。W5 起採「單項小步」:
> 每項獨立 checklist、獨立 commit,不再整波綁定。

## W5 設計決策

| # | 決策 | 理由 |
|---|---|---|
| W29 | **sid 預派(claude)**:dispatcher 於 attempt **開跑前**持久化 `attempts+1` 與預派 session id(uuid),journal `attempt_started`;runner 收 `preassigned_session_id`(claude `--session-id`;codex thread id 由 CLI 自生無法預派) | 冪等盤點 #5:harness 於 attempt 中途死 → 原本重跑重花錢;預派後 claude 可 native resume 續跑;快照器首 attempt 也拿得到 sid |
| W30 | **crash 偵測** = `attempts>0 且 a{attempts}.envelope.json 不存在`(envelope 驅動,與三態同一philosophy):claude+sid → **退還該 attempt** + resume 續跑(transcript 重放不重工);codex/無 sid → **UNKNOWN 交人**(不能證明副作用) | 「loop on evidence」:有證據能續就續,不能證明就交人 |

## Checklist

**W5.1 — sid 預派 + crash 偵測(W29/W30)** ✅
- [x] inner_runner `run_attempt(..., preassigned_session_id=None)`;job 傳遞;
      rawcli runner:`session_id = resume or preassigned`、`resume=bool(resume)`
- [x] dispatcher while 迴圈:attempt+1 與預派 sid(rawcli+claude 且無 sid)
      **先 upsert 再 spawn**;journal `attempt_started`
- [x] dispatcher 進場 crash 偵測(`a{N}.envelope` 缺):**有 sid(任一引擎,
      codex resume 亦已實證)→ 退還 attempt + resume**;無 sid(codex 首跑)
      → UNKNOWN 交人 + comment(W30 較計畫放寬:sid 有無為準,非引擎為準)
- [x] snapshotter 限制解除(claude 首 attempt 即有 sid);DESIGN_idempotency
      #5 標已修(含殘邊角:persist→spawn 間 crash → resume 失敗以 error 收場,
      機率極低接受)
- [x] 測試:test_idempotency +3(預派 spawn 前已落 store、crash 有 sid 退還
      resume、無 sid UNKNOWN)全綠;9 個 fake fork 簽名 +**kw 同步;
      20 測檔 + selftest + e2e_gate/dashboard 全綠
- [x] commit+push

## 後續候選(未排程,擇需)

- E3 evict/rehydrate + 實時 killpg(需異步架構)
- openhands-acp/server 的 codex 對照(quota)
- landlock / docker 隔離實作(W22 介面已就緒)
- 量產 python 標準結構(另開 repo)
- dashboard 後續 UI(shutdown 按鈕等,湊批再做)
