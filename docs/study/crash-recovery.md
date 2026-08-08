# Crash → Resume 崩潰復原(研究結論)

> raw supervisor 包 `claude -p` / `codex exec`,在受控時機殺掉再 resume,claude 與 codex 的 2×2 崩潰矩陣**皆實測全過、不重工**;真正的教訓不是「resume 能不能接回」,而是「**別信 exit code、別信事件流、別信 agent 自稱 done**」——復原必須建在證據上,而非程序訊號上。

## 研究問題

ARCP 的差異化價值集中在「跨 CLI 一致的 session 層級 crash recovery」。文獻背景讓這題更值得做:2026-06 的 435 篇編碼 agent 文獻中**僅約 6% 實作任何 rollback 機制**,且**沒有任何研究報告 state 損毀後的復原成功率**——recovery/rollback 被判為「最未被解決的能力」(原文:✅ 3-0)。OpenHands 的 crash recovery 只對自家 agent loop 完整,對 ACP 外部 agent 只保存自己側的 session 引用。

因此本輪要用真實負載回答四個具體問題:

1. `claude -p` / `codex exec` 被殺後,原生 `--resume` / `exec resume` 能否可靠重接**原對話**、且**不重工**?
2. 兩家 CLI 的 session id 取得路徑不同(claude 可預指定、codex 不行),對 supervisor 端持久化有何影響?
3. 「程序結束」等不等於「任務完成」?什麼訊號可以當完成證據?
4. 原生 resume 失敗時(換目錄、太早死沒抓到 id),有沒有降級路徑?

## 方法 / 實驗

核心 harness 是 `examples/jira-agent-poc/recovery_test.py`(§9.3-1),它復用 PoC 的 drivers + Supervisor,所以矩陣通過同時也驗證了 driver 只草擬過的 resume 路徑。

- **任務**:依序建立 step1.txt~step5.txt,每檔內容依賴前一檔(step3.txt = `123`),每建一檔 `sleep 3` 再繼續,全部完成回 `ALL_DONE`——刻意造出「有序、可查、有中途進度」的依賴鏈。
- **2×2 矩陣**:phase = `early`(第一個 agent 動作、step1.txt 尚未存在,「思考中、無耐久進度」)/ `midtool`(step2.txt 出現後 1s、正落在 `sleep 3` 內,「工具執行中、部分進度」);signal = `SIGTERM`(supervisor 式優雅停止)/ `SIGKILL`(硬崩、無清理)。
- **每 case 四項確定性判準**:C1 resume run 抵達非錯誤 `result` 事件;C2 resume 事件流帶**原 session id**;C3 step1..step5 皆存在且內容鏈正確;C4 崩潰前寫的檔**不被重寫**(mtime 不變)。
- 另有 `workspace_recovery_test.py`(workspace 搬家情境)、`grader.py`(證據型停止)、`resume_transcript.py`(transcript 降級)、`recovery_loop.py`(自動 recovery 迴路)四支配套,共同組成三段梯度。

## 結論(帶真實數據 / 事實)

- **claude 基線 4/4 全過(2026-08-01)**:2×2 四 case PASS、**16/16 判準**全綠;總成本 **$0.185**(haiku),單 case $0.03–0.07。真實事件流落 `fixtures/claude_p_{crash,resume}_real.jsonl`,並用 replay 管線回歸(crash 流停 `running`、resume 流到 `done`)。改用 **killpg(殺整個 process group)複驗仍 4/4**。
- **codex 基線亦全過(2026-08-02)**:thread id **無法預指定**,但從 `thread.started` 事件**事後擷取**來得及(連在 `turn.started` 時殺都擷取得到)→ `codex exec resume <id>` 重接成功。early×SIGTERM/SIGKILL、midtool×SIGKILL 先過,midtool×SIGTERM 於睡眠 artifact 釐清後**補測 2/2 乾淨 PASS**,2×2 補齊。
- **⚠️ SIGTERM rc=0 假完成陷阱(最重要的發現)**:codex 收 SIGTERM 會**優雅退場 rc=0**。原本 supervisor 的「事件 OR exit code」雙判據(`_finalize_on_exit`:rc==0→DONE)會把**被中斷的 run 誤判成 DONE**。雙判據只能判「程序結束」,不能判「任務完成」——**exit code 不能當完成證據**,證據型停止(§9.3-2)因此從「加分項」升級為「必要項」。
- **killpg 是必要而非優化**:codex 的 zsh 子程序在只殺父程序時會**孤兒續跑**,任務在 supervisor 背後被偷偷做完;kill 必須 killpg。
- **事件流不可當進度真值**:codex 工具粒度 / 指令服從度變異大(同 prompt 有時單指令打包五步、有時逐步、有時無視 sleep)——**進度真值要以檔案系統 / 工作區為準,不是事件流**。
- **resume 子命令的 argv 坑**:`codex exec resume` **不吃 `--sandbox`**(rc=2),要改用 `-c sandbox_mode="..."`;driver 已修。
- **睡眠凍結計時器**:實驗機系統睡眠會凍結 supervisor 計時器,產生**假 stall / 假 hang**;live 監督要防睡或跑在 server。（ARCP 現行做法:⚠️不 caffeinate、靠 timebox 迭代、睡醒能續,見 [decisions D14](../decisions.md)。）
- **workspace 搬家會打死原生 resume,但降級救得回(4/4 PASS)**:claude session store 綁**啟動時 cwd**(`~/.claude/projects/<編碼路徑>/`),workspace `mv` 之後原生 resume 死於 `No conversation found with session ID`(2.1.206 實錄);但 ARCP journal 跟著 journal_root 走、不綁 cwd,**transcript 降級開新 session 續跑、不重工**救回。git worktree 為同機制(worktree 路徑即不同 cwd),未另測。
- **transcript 降級 resume 實測成立**:`resume_transcript.py`(固定 marker、總量 60k 砍舊留新、逐訊息 8k 截斷,設計抄自 OpenHands §6.4)。claude midtool×SIGKILL **不用原生 resume**、從 journal 渲染 transcript 開全新 session → 4/4 判準 PASS(含**不重工**——新 session 無記憶,全靠 transcript 告知進度)。此路徑同時解決「codex 太早死、thread id 沒擷取到」的無 id 情境。
- **自動 recovery 迴路端到端修復兩種故障(live)**:`recovery_loop.py`(run → grade → 依梯度升級 resume,同一 rung 不重試、有上限;grader 必備——loop on evidence)。場景一:claude midtool×SIGKILL 硬 crash → 迴路 native resume 修復 `initial:failed → native:done`;場景二:**codex midtool×SIGTERM 的 rc=0 假完成被 grader 否決(`evidence FAIL: missing step3/4/5`)→ 迴路自動 resume 原 thread 補完**。
- **證據型 grader 是唯一被批准的 sticky 終端狀態例外**:`grader.py`(FileChecklistGrader / CommandGrader / AllOf,Verdict 附理由入 journal),DONE 需過證據,**證據不過即覆寫 FAILED**——證據高於自稱。selftest 14/14 含「DONE 流 + 證據缺 → FAILED」;recovery_test 的 C3 已 dogfood 此 grader。
- **待驗缺口(原文明列)**:長跑 / 大 context 下的 resume 尚未做;git worktree 情境未另測;codex 載入使用者 plugin 造成的行為變異,對照實驗宜 `--ignore-user-config`(未實測)。

## 兩家 CLI 對照(claude vs codex 的 resume 路徑差異)

| 面向 | claude(`claude -p`) | codex(`codex exec`) |
|---|---|---|
| session id 取得 | `--session-id <uuid>` **可預指定**,程序啟動前即持久化 `(session_id, cwd)`,不必等回傳 | **不可預指定**;須從 `thread.started` 事件**事後擷取**(turn.started 時殺仍抓得到) |
| resume 指令 | `claude --resume <id>` | `codex exec resume <id>` |
| 終止語意 | 有明確 `result` 事件(帶 cost/usage/permission_denials/terminal_reason) | **無獨立終止事件**;靠 `turn.completed` + process exit |
| exit code 可信度 | `result` 事件為主判據 | **⚠️ SIGTERM 也 rc=0**,被中斷仍像 DONE——不可信 |
| resume argv 坑 | `bypassPermissions`/`plan` 永不還原;`--mcp-config`/`--settings`/`--add-dir` 需重傳 | `exec resume` **不吃 `--sandbox`**(rc=2),改用 `-c sandbox_mode="..."` |
| kill 方式 | killpg 複驗 4/4 | **必須 killpg**,否則 zsh 子程序孤兒續跑 |
| session 儲存位置 | `~/.claude/projects/<編碼 cwd>/`,**綁啟動時 cwd**(搬家即失效) | `~/.codex/sessions/`(JSONL 落地;workspace 搬家未對 codex 測) |
| 2×2 矩陣結果 | **4/4 case、16/16 判準 PASS**,$0.185(haiku) | **4/4 case PASS**(2×2 補齊) |

## 對 ARCP 的影響(這些結論如何形成了實作)

- **證據型停止取代自稱 done**:SIGTERM rc=0 假完成把「證據型 grader」從加分項打成必要項。實作落地為 `grader.py` + supervisor 掛 grader,DONE 未過證據即覆寫 FAILED——這是 sticky 終端狀態唯一被批准的例外。
- **native resume 為第一路徑,並以持久化承載**:claude 端因 `--session-id` 可預指定,supervisor 直接持久化 `(session_id, cwd, 旗標)`;codex 端補上「從 `thread.started` 事後擷取 thread id」的路徑。`snapshot.json` 成為 stateless supervisor 的重建依據(掃非終止 run → 比對 PID/session → attach 或 resume)。
- **killpg evict 寫進控制面**:因孤兒子程序會在背後偷做完任務,kill 一律 killpg(殺整個 process group),否則 evict / 中斷不具真實效力。
- **三段梯度 recovery(比 OpenHands 實際接線多一階)**:**原生 resume → transcript 注入 → 全新重跑**。前兩階皆有實測。transcript 降級的價值在真實陷阱上被證明(workspace 搬家打死原生 resume 時救回),且同時涵蓋「codex 太早死、無 id」情境。設計抄自 OpenHands 的 `resume_transcript.py`(marker / 截斷策略),但**不引入 OpenHands 依賴**——ARCP 本就 journal 全部事件(`events.jsonl`),素材齊全。
- **事件流只當觀測、檔案系統當真值**:因 codex 事件粒度不可靠,watchdog / 進度判定以工作區真值為準;waiting-permission 也改為盯事件流中的 denial,而非偵測卡住。
- **自動 recovery 迴路把上述全部接成閉環**:`recovery_loop.py` 已 live 驗證能自動抓到並修復「硬 crash」與「rc=0 假完成」兩種最硬的故障,同一 rung 不重試、必配 grader(loop on evidence)。

## 原始出處

- 主源(crash recovery 實驗與矩陣、§5.2 終止語意、§6.4 ACP resume 對照、§9.3 PoC 清單):[../../research/2026-08-agent-runtime-control-plane-research-v3.md](../../research/2026-08-agent-runtime-control-plane-research-v3.md)
- 前版(市場缺口 / rollback 文獻量化 / resume 目錄範圍與 CLI 陷阱清單):[../../research/2026-07-agent-runtime-control-plane-research.md](../../research/2026-07-agent-runtime-control-plane-research.md)
- 實驗 harness 設計(2×2 矩陣、C1–C4 判準、session-id 不對稱):`examples/jira-agent-poc/recovery_test.py`(docstring)、配套 `workspace_recovery_test.py` / `arcp_poc/{grader,resume_transcript,recovery_loop}.py`
