# PLAN_wave6 — Server 頁 + evict 正名 + transcript 事件化 + REST 文件

> 承 W5(dashboard 三 tab + 零外部依賴)+ 使用者 2026-08-07 口述需求(見
> REQUIREMENTS §10)。**單線、小步、每 phase commit+push。不用 caffeinate。**
>
> 已拍板決策(2026-08-07 反問確認):
> - transcript = **純事件+按鈕,移除 60s 定時快照**
> - dashboard 綁 **0.0.0.0 無認證**(內網信任;金鑰只顯示狀態不顯值)
> - process 對應 = **best-effort ps**(cwd→workspace→Jira,純 stdlib)
> - 需求文件 = **單一 requirements.md**

## W6 決策表(新標 W)

| # | 決策 | 理由 |
|---|---|---|
| W31 | Server 頁系統/程序資訊全用 **stdlib + subprocess**(ps/vm_stat/sysctl/sw_vers),不裝 psutil | 內網零外部依賴(W5.9);已驗 loadavg/disk_usage/版本全拿得到 |
| W32 | 登入/金鑰**只顯示狀態**(檔案存在/到期),絕不顯示值 | 安全底線(不受「無認證」決策影響) |
| W33 | dashboard 綁 host = config(CLI arg/env),**預設 0.0.0.0**;control API host 已是 routes.yaml config | 使用者選內網開放;但保留一鍵切回 127.0.0.1 + 文件標寫入端點風險 |
| W34 | evict **正名「強制驅逐」**+ store 計數 `evict_count`(per-session)+ journal 已有 `evicted` | evict 是異常處理,頻率是健康指標 |
| W35 | transcript **移除 snapshotter 定時**,改事件觸發(dispatcher/commands 既有點)+ Jira 頁按鈕(`POST /gen_transcript/<id>`);每份存 `<name>.meta.json`(ts+reason+session+subs) | 使用者:定時產 in-progress 太耗;要知每份何時/為何產 |
| W36 | REST 文件自寫 `/docs`(HTML,零外部)+ `/openapi.json`;連結放 Server 頁 | Swagger UI 需外部 CDN,違反內網原則 |
| W37 | 連線 IP:BaseHTTPRequestHandler 記 `client_address` + 時間進環形緩衝(記憶體,上限 N 筆);Server 頁顯示 | 內網開放後要知誰在連;history 不落盤(重啟清,夠用) |
| W38 | REST 文件改 **vendored Swagger UI**(swagger-ui-dist 5.32.12,Apache-2.0,~1.7MB)+ 手寫 `/openapi.json`;`/docs` serve Swagger UI | 使用者指示 vendor 回來;評估確認自包靜態檔、離線可用、美觀可 try-it-out(取代原手寫 /docs) |
| W39 | per-ticket 詳情頁「事件時間軸」= journal 事件 → **重用 vendored vis-timeline**;harness 寫 Jira 時補 journal `jira_write`(留言/assign/transition) | 使用者要「何時留言/改 status」的時間軸;資料在 journal 已有,補記寫入點讓時間軸更清楚 |

## Checklist

**W6.0 — requirements.md + PLAN_wave6** ✅
- [x] requirements.md(需求總表,含 Why,永久維護規則)
- [x] PLAN_wave6.md(本檔)
- [ ] commit+push

**W6.1 — Server 頁核心(系統/版本/登入狀態/資源)**
- [ ] `sysinfo.py`(harness 或 dashboard 端):OS(sw_vers/uname)、版本(claude/
      codex/python `--version`)、登入狀態(~/.codex/auth.json 存在、claude 設定;
      **只回布林/到期,不回值**)、loadavg/uptime/mem(vm_stat)/disk(shutil)、
      異常旗標(disk<10%、load 過高)
- [ ] dashboard 新 `/server` 頁 + 第 4 tab(🖥 Server);`/server/data` JSON
- [ ] dashboard 綁 host = argv/env(預設 0.0.0.0);啟動訊息顯示綁定位址 + 風險提示
- [ ] e2e:/server 頁渲染、/server/data 欄位齊、金鑰值不外洩(只狀態)
- [ ] commit+push

**W6.2 — per-process + per-workspace**
- [ ] ps 掃描:列 claude/codex 進程(pid/%cpu/rss);以 `lsof -p`/cwd 對應
      workspace path → Jira(best-effort,對不上分開列)
- [ ] per-workspace(掃 active session):workspace path、skill folder 名、session_id、
      sub-session(subagents/agent-*.jsonl glob)、transcript 產物、`du` 磁碟用量、
      跑時間(attempt_started→now)
- [ ] Server 頁 render + e2e(假資料驗欄位/對應邏輯)
- [ ] commit+push

**W6.3 — evict 正名 + 計數 + 說明**
- [ ] store `evict_count`(migration);dispatcher evicted 時 +1
- [ ] ticket 頁 Evict 按鈕正名「強制驅逐(killpg)」+ hover title(何時用/如何恢復)
- [ ] Server 頁「異常」區:總 evict 次數 + 各票次數;`GET /status` 加 evict 統計
- [ ] 單元測:evict_count 遞增、migration
- [ ] commit+push

**W6.4 — transcript 事件化 + metadata(移定時)**
- [ ] 移除 snapshotter 定時(run_poller 不再起 Snapshotter thread;檔案/類別保留
      或刪,註記)；`snapshot_interval_sec` 廢棄註記
- [ ] transcript.py:`finalize` 寫 `<name>.meta.json`(ts/reason/session/subs);
      新 `generate(session,engine,ws,reason)` 統一入口(事件+按鈕都走它)
- [ ] 事件觸發已在 dispatcher/commands(handoff/inactive/close/evict)——確認都帶
      reason;等人類的票(pending)也產一次
- [ ] 被動:`POST /gen_transcript/<id>`(control API 或 dashboard→control);ticket
      頁 Transcript 卡加「產生」按鈕 + 顯示現有產物的時間/原因(讀 meta.json)
- [ ] cclog codex 實測(真 codex session → HTML);sub-session 不漏
- [ ] 單元測 + e2e(meta.json 內容、按鈕、卡片顯示)
- [ ] commit+push

**W6.5 — REST 文件(vendored Swagger UI + /openapi.json)**
- [ ] vendor swagger-ui-dist 5.32.12(Apache-2.0)→ `tools/vendor/swagger-ui/`
      (swagger-ui.css + swagger-ui-bundle.js;NOTICE 記版本/授權/出處)
- [ ] `GET /openapi.json`(手寫 spec,涵蓋 dashboard + control API 所有端點;
      寫入端點 description 標 ⚠️)
- [ ] `GET /docs` = Swagger UI HTML(讀本地 /openapi.json + /swagger-assets/*);
      `/swagger-assets/<file>` 路由服務 vendored 檔;CSP 放行同源
- [ ] Server 頁放「REST API 文件」連結
- [ ] e2e:/docs 200、/openapi.json valid、swagger 資產本地服務、含所有端點
- [ ] commit+push

**W6.7 — 事件時間軸(per-ticket,重用 vis-timeline)**
- [ ] harness 寫 Jira 時補 journal `jira_write`(action=comment/assign/transition,
      target/摘要);dispatcher/approval/commands 的 add_comment/assign 點都補
- [ ] ticket 頁「事件時間軸」分頁/卡:讀該票 journal → vis-timeline items
      (grouped:Jira 生命週期 / attempt);時間戳人類可讀;無 vis-timeline 時
      降級成時間排序清單
- [ ] 時間軸範圍照決策(預設 harness/Jira 生命週期;agent 對話留 transcript)
- [ ] e2e:時間軸元素存在 + jira_write 事件入 journal
- [ ] commit+push

**W6.6 — 連線 IP 追蹤 + history**
- [ ] dashboard + control API:Handler 記 client_address + path + ts 進環形緩衝
      (deque maxlen);`/server/data` 含「目前連線 + 近期 N 筆 history」
- [ ] Server 頁顯示;e2e(連幾次後 history 有記錄、含 IP)
- [ ] commit+push

## W6 里程碑
**M17 = 運維可視**:Server 頁一眼看機器健康 + 每 agent 資源 + 連線;evict 異常可追蹤。
**M18 = 可維護**:transcript 事件化省 loading + 每份可溯來源;REST 有文件;需求有總表。

## 明確不做(留後續)
- control API 加認證(使用者選無認證;綁定可切回 127.0.0.1 為緩解)
- process 對應改造 harness 記 PID(best-effort ps 已夠 v1)
- 連線 history 落盤(記憶體環形緩衝夠用)
