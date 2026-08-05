# PLAN_wave4 — transcript 可視化 + dashboard 強化 + script trigger 萬用化

> 承 W3(M13/M14)。需求 = 使用者 2026-08-06 五項(script trigger 萬用/log 保存、
> transcript HTML 可視化、close 打包下載、active 每分鐘快照、dashboard 分頁/filter/
> 欄位/bug)。**單線、小步、每 phase commit+push。斷線 resume:讀本檔 + git log。**
>
> 已定案(反問確認 2026-08-06):
> - **統一快照器**:active 每 N 秒重產 HTML(config,**預設 60s**)+ 離手事件
>   (換手/交人/pending/close)當下產 final + close 額外打包——B1/B2/B3 一套機制。
> - **打包** = 最高壓縮率 tgz(gzip -9);agent transcript 與 script log 共用設施。
> - **工具(2026-08-06 改案)** = **vendor `claude-code-log`**(MIT,Daniel Demmel,
>   https://github.com/daaain/claude-code-log,本機 clone `~/git/claude-code-log`
>   v1.5.0):把必要模組 copy 進 `harness/tools/cclog/` 作適當 refactor,**註明出處**。
>   取代原 cchv-server prebuilt 方案 —— V0 實測發現 cchv `--export` **刻意丟棄
>   sidechain**(source `export.rs:330` 證實),且 Rust binary 無法 refactor;
>   claude-code-log 是 Python(同棧)、**原生渲染 sidechain/agentId(sub-agent)**、
>   `--provider codex --session-id` 支援 codex(beta)。
> - 順序:V0 除險 → C 快贏 → B2 基礎設施 → B1+B3 快照器 → A script trigger。

## W4 設計決策(新標 W)

| # | 決策 | 理由 |
|---|---|---|
| W23 | **(改案)vendor claude-code-log 必要模組**進 `harness/tools/cclog/`(MIT、出處/版本註明於 NOTICE);剝離 TUI/browser/git 等不需要的部分,只留 parse→render HTML 鏈路;依賴裝進 `tools/cclog/.venv` 專用 venv(不污染其它);我們的薄 wrapper `render_transcript.py` 直呼函數(claude session / subagents/agent-*.jsonl / codex provider) | 使用者 2026-08-06 改案:「copy 到我們的專門資料夾,作適當 refactor,註明出處」;Python 可控、離線可用、版本釘住 |
| W24 | 快照產物放 instance 內:`<base>/transcript/`(latest.html、final.html、close 打包 `transcript.tgz`);dashboard 以靜態檔服務 + 下載連結;retention 回收時一併清 | 產物跟 instance 同生命週期,W3.3 現成回收 |
| W25 | 快照器 = harness 內背景 daemon thread(持 store 引用,掃 active session→cchv --export);離手/close 的 final 由 dispatcher 事件點同步觸發 | 同步 poll 架構中 attempt 執行時主線程被佔,每分鐘快照必須背景做 |
| W26 | dashboard 分頁+filter 走**前端 JS**(server 照 render 全表,JS 做分頁/status 下拉/keyword);detail 頁去 `<meta refresh>` 改 **fetch 局部更新**(修 auto-collapse bug) | 票量數百級 JS 夠用,免 API 改動;整頁重載是收起 bug 根因 |
| W27 | script trigger:trigger 配 `script:`(與 profile 互斥),支援任意執行檔(uvx/npx/.sh/.py……直接 argv 執行);cwd=獨立 run 資料夾 `{trigger}__{run_name}__{ts}`;stdout/stderr 各存檔;結束 tgz(gzip -9)+ 下載;timeout/journal 同 trigger 語意 | 使用者:「做得比較萬用一點」——不 hardcode 直譯器,argv 直接執行 |
| W28 | dashboard 新欄位:assignee(displayName,poller watch 順存)、created(watch first_seen)、finished(session finished_at)、**最新換手起點**(journal 最近一次 handoff/inactive_cleared 後首個 attempt 時間) | 使用者指定;資料多半現成(watch/session/journal) |

## Checklist

**Phase W4.0 — V0:transcript 工具落地驗證(除最大風險)**
- [x] 研究(2026-08-06):cchv-server prebuilt 實測可產 HTML(claude session-id +
      codex 絕對路徑),**但 source 證實 --export 丟 sidechain** → 棄用;
      改案 vendor claude-code-log(MIT/Python/原生 sidechain/codex beta)
- [x] sub-agent 枚舉方式確定:新版 Claude Code 子代理在
      `<proj>/<session-id>/subagents/agent-<id>.jsonl` 獨立檔(glob 即得
      sub-agent id);列入 DESIGN_transcript.md
- [x] vendor:`claude_code_log/` **整包 zero-diff copy** → `harness/tools/cclog/`
      (內部耦合深,拆片段風險高;NOTICE.md 註明出處/v1.5.0/commit 0a3327d/MIT
      + LICENSE.upstream;2.1MB/58 檔進 git);依賴裝 `tools/cclog/.venv`
      (gitignore;NOTICE 記重建指令)
- [x] 薄 wrapper `render_transcript.py`(ARCP 自寫,不混上游):session id→檔案
      定位、subagents glob、codex rollout→thread id;subprocess 呼 vendored cli
- [x] 真 session 實測三種全通:claude SCRUM-22(229KB)、claude+**72 個
      sub-agent HTML**(travel-osaka)、codex e2e thread(245KB);樣品已交付
- [x] 清掉 cchv(prebuilt 下載已 rm;未進過 git)
- [x] commit+push

**Phase W4.1 — C:dashboard 分頁+filter+欄位+bug 修** ✅
- [x] index 加欄:assignee(watch 新欄 `last_assignee` displayName,poller/adopt
      順存,store migration)/ created(first_seen_ts)/ finished(finished_at)
      / 最新換手起點(journal handoff+inactive_cleared 最近 ts)
- [x] 前端 JS:分頁(10/20/50/100,預設 20)+ status 下拉(值自表格收集)+
      keyword 搜尋;狀態存 localStorage
- [x] **index 也去 meta refresh** → fetch 局部更新(只換統計卡+表身,輸入框
      不被打斷);detail 頁 fetch 局部更新 + `<details>` 展開狀態按序還原 +
      tab 保留(**auto-collapse bug 修**)
- [x] `e2e_dashboard.py` 22 checks(+7:新欄位/工具列/無 meta refresh/局部更新
      JS/detail 展開保留)全 PASS;全套回歸綠
- [x] commit+push

**Phase W4.2 — B2:close 打包 + final HTML + 下載連結** ✅
- [x] `arcp_harness/transcript.py`:`snapshot→latest*.html`、`finalize→
      final*.html(+sub-*)`、`pack→transcript.tgz`(tarfile gzip
      compresslevel=9:主/子 session jsonl 原檔 + final HTML);renderer =
      tools/cclog wrapper 直接 import(注入點供測試);缺席/失敗優雅降級
- [x] dispatcher 終態點(SUCCESS/FAILURE/UNKNOWN)呼 `_pack_transcript` +
      journal `transcript_packed`(ABORTED 走 commands/external,留 W4.3 離手
      快照涵蓋)
- [x] dashboard ticket 頁 transcript 卡(HTML target=_blank / tgz download)
      + `/tfile/<iid>/<name>` 靜態服務(basename 白名單防 traversal)
- [x] `test_transcript.py` 6 tests(fake renderer 注入)全綠;e2e_dashboard
      26 checks(+4:卡連結/HTML 可讀/tgz header/traversal 404)全 PASS
- [x] commit+push

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
