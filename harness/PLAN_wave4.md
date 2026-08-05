# PLAN_wave4 — transcript 可視化 + dashboard 強化 + script trigger 萬用化

> 承 W3(M13/M14)。需求 = 使用者 2026-08-06 五項(script trigger 萬用/log 保存、
> transcript HTML 可視化、close 打包下載、active 每分鐘快照、dashboard 分頁/filter/
> 欄位/bug)。**單線、小步、每 phase commit+push。斷線 resume:讀本檔 + git log。**
>
> 已定案(反問確認 2026-08-06):
> - **統一快照器**:active 每 N 秒重產 HTML(config,**預設 60s**)+ 離手事件
>   (換手/交人/pending/close)當下產 final + close 額外打包——B1/B2/B3 一套機制。
> - **打包** = 最高壓縮率 tgz(gzip -9);agent transcript 與 script log 共用設施。
> - **工具** = claude-code-history-viewer 的 `cchv-server` prebuilt,裝
>   `harness/tools/`(不 global);`--export <session|/abs/path.jsonl> --format html`
>   headless 產 HTML;claude(~/.claude/projects)+ codex(~/.codex/sessions)都支援
>   (WebFetch 查證)。
> - 順序:V0 除險 → C 快贏 → B2 基礎設施 → B1+B3 快照器 → A script trigger。

## W4 設計決策(新標 W)

| # | 決策 | 理由 |
|---|---|---|
| W23 | cchv-server prebuilt 放 `harness/tools/`,**binary 不進 git**(.gitignore);`tools/README.md` 記版本+下載指令(或 install 腳本),斷線可重建 | 使用者:prebuilt、裝自己資料夾不 global;repo 不塞 binary |
| W24 | 快照產物放 instance 內:`<base>/transcript/`(latest.html、final.html、close 打包 `transcript.tgz`);dashboard 以靜態檔服務 + 下載連結;retention 回收時一併清 | 產物跟 instance 同生命週期,W3.3 現成回收 |
| W25 | 快照器 = harness 內背景 daemon thread(持 store 引用,掃 active session→cchv --export);離手/close 的 final 由 dispatcher 事件點同步觸發 | 同步 poll 架構中 attempt 執行時主線程被佔,每分鐘快照必須背景做 |
| W26 | dashboard 分頁+filter 走**前端 JS**(server 照 render 全表,JS 做分頁/status 下拉/keyword);detail 頁去 `<meta refresh>` 改 **fetch 局部更新**(修 auto-collapse bug) | 票量數百級 JS 夠用,免 API 改動;整頁重載是收起 bug 根因 |
| W27 | script trigger:trigger 配 `script:`(與 profile 互斥),支援任意執行檔(uvx/npx/.sh/.py……直接 argv 執行);cwd=獨立 run 資料夾 `{trigger}__{run_name}__{ts}`;stdout/stderr 各存檔;結束 tgz(gzip -9)+ 下載;timeout/journal 同 trigger 語意 | 使用者:「做得比較萬用一點」——不 hardcode 直譯器,argv 直接執行 |
| W28 | dashboard 新欄位:assignee(displayName,poller watch 順存)、created(watch first_seen)、finished(session finished_at)、**最新換手起點**(journal 最近一次 handoff/inactive_cleared 後首個 attempt 時間) | 使用者指定;資料多半現成(watch/session/journal) |

## Checklist

**Phase W4.0 — V0:cchv-server prebuilt 落地驗證(除最大風險)**
- [ ] 下載 prebuilt 到 `harness/tools/cchv/`;.gitignore binary;`tools/README.md`
      記版本/下載指令/用法
- [ ] 真 session 實測:claude(近期 SCRUM run 的 session id 或 aN.raw.jsonl)
      `--export --format html` 產出可開 HTML;codex(thread id)同
- [ ] 探索 sub-agent session 枚舉方式(claude Task 子 session 怎麼從主 session 找)
      → 記錄到 `DESIGN_transcript.md`(可行性、指令、限制)
- [ ] commit+push(文件與腳本;binary 不進)

**Phase W4.1 — C:dashboard 分頁+filter+欄位+bug 修**
- [ ] index 表格加欄:assignee / created / finished / 最新換手起點(W28 資料源)
- [ ] 前端 JS:分頁(每頁數量可設定,預設 20)+ status 下拉 filter + keyword 搜尋
- [ ] detail 頁去 meta refresh → fetch 局部更新(保展開/捲動);index 頁保留自動更新
- [ ] E2E `e2e_dashboard.py` 擴:新欄位渲染、filter/分頁 DOM 存在、detail 無 meta refresh
- [ ] commit+push

**Phase W4.2 — B2:close 打包 + final HTML + 下載連結**
- [ ] `arcp_harness/transcript.py`:`snapshot(session)→latest.html`、
      `finalize(session)→final.html + transcript.tgz(gzip -9,含主/子 session
      jsonl + html)`;cchv 缺席時優雅降級(journal 警告,不擋流程)
- [ ] dispatcher 終態點(SUCCESS/FAILURE/UNKNOWN/ABORTED)呼 finalize
- [ ] dashboard ticket 頁:transcript 卡(latest/final HTML 連結 + tgz 下載)
- [ ] 單元測 `test_transcript.py`(fake cchv 腳本):產物路徑/tgz 內容/降級
- [ ] commit+push

**Phase W4.3 — B1+B3:快照器(active 每 N 秒 + 離手 final)**
- [ ] `snapshot_interval_sec` config(預設 60);背景 daemon thread 掃 active
      session → snapshot;run_poller 起停
- [ ] 離手事件點(handoff 換 agent/交人、assignee 交人 inactive、pending 交人)
      同步 finalize(輕量版:產 final.html,不打包——打包只在 close)
- [ ] 單元測:interval 觸發、離手觸發、thread 起停乾淨
- [ ] commit+push

**Phase W4.4 — A:script trigger 萬用化 + log 保存/tgz**
- [ ] trigger config `script:`(argv list 或字串)與 `profile:` 互斥;cwd=
      `runs/{trigger}__{run_name}__{ts}/`;timeout;run_name 校驗沿用
- [ ] 執行:stdout/stderr 各自存檔(`stdout.log`/`stderr.log`);結束 rc/耗時
      journal;tgz(gzip -9)+ dashboard 下載連結;oneshot CLI 同支援
- [ ] dashboard:script run 列表/詳情(log 檢視 + tgz 下載)
- [ ] 單元測 `test_script_trigger.py`:.sh/.py 真跑(本機)、stdout/stderr 保存、
      tgz 內容、rc 非零=FAILURE、timeout
- [ ] commit+push

## W4 明確不做(留後續)

- cchv-server 的 `--serve` WebUI 模式(我們只用 --export;dashboard 自己的)
- transcript HTML 的自製 renderer(全交 cchv;它壞了才考慮 fallback 自寫)
- script trigger 的 sandbox 隔離(W22 介面尚未實作 docker;script 信任 config 作者)
- dashboard server 端分頁 API(票量破千再議)

## W4 里程碑

**M15 = 可視化閉環**(W4.0-W4.3):任何時刻(active/離手/close)都有人類可肉眼
看的 transcript HTML;close 有打包下載。
**M16 = 萬用觸發**(W4.4):trigger 跑任意 script,log 全保存可看可下載。
