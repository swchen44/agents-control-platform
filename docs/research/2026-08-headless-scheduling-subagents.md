# headless CLI × 排程/subagent 風險研究 — claude -p / codex exec 與 ARCP 的相容性

> 問題:profile 若指示 agent 開 **subagent**、或用 **Monitor / loop / schedule** 等
> 「之後才發生」的機制(例如等長時間 build),對 `claude -p` / `codex exec` 這種
> one-shot headless 執行會不會衝突——尤其是「**提早回傳讓 harness 以為結束,
> 其實後面還有排程要跑**」。事實基礎:官方文件 + GitHub issue + **本機三個實驗**
> (指令與數據全附,可重跑)。2026-08-11。實驗環境:Claude Code CLI **2.1.206**、
> codex-cli **0.142.5**、macOS(Darwin 24.6.0)、**非 TTY 父行程**(與 ARCP
> supervisor 生 `claude -p` 的情境相同)、model=haiku。

## 0. 一句話

**subagent 沒事**(`claude -p` 會等全部 subagent 跑完才回傳、費用合併計入);
**session 排程與背景工作有事**——實測 `-p` 內建的排程「建立成功」但行程一退出就
**靜默死亡永不執行**,背景 Bash 在主回覆後 ~5 秒寬限即被殺。「提早回傳」的擔心
成立,但形態更糟:不是「之後還會跑」,是「**之後永遠不會跑、也沒有任何錯誤**」。
ARCP 的證據型停止能兜住誤判(verify 不過=FAILURE),代價是浪費 attempt;
一行環境變數(`CLAUDE_CODE_DISABLE_CRON=1`)可整類消除排程風險。
codex 補測(同日):**5 分鐘 build 前景等待完整成功**(單一 tool call 300s 無
timeout)、**`exec resume` 可靠**(暗號回收+跨目錄皆過,argv 坑仍在但 rawcli
已繞);等待期間事件流**完全靜默** → `stall_seconds` 必須 0 或大於最長單一命令。

## 1. 機制盤點(文件 + GitHub 一手資料)

### 1.1 Claude Code 的「之後才發生」機制分三種(官方 scheduled-tasks 文件)

| 機制 | 跑在哪 | 行程結束後還活著? | 對 `claude -p` 的意義 |
|---|---|---|---|
| `/loop`、CronCreate/CronList/CronDelete | 本機、**session 範圍**,只在行程存活且 idle 時執行 | ❌ | 🔴 一回合結束即退出 → 排程**靜默失效**(實驗 3 實證) |
| 背景 Bash(`run_in_background`) | 行程內 | ❌(主回覆後 ~5s 寬限) | 🔴 長工被殺(實驗 2 實證) |
| Monitor tool / 前景等待 | 行程內、**擋住不回傳** | —(等完才回) | ✅ 安全:等待發生在單一 attempt 內 |
| Cloud Routines(`/schedule`) | Anthropic 雲端 | ✅ **不依賴本機行程** | ⚠️ 反向風險:真的會跑,但在 harness 視野外(無 trace/預算/verify) |

其他要點:session 排程 7 天過期、每 session 上限 50 個;`CLAUDE_CODE_DISABLE_CRON=1`
可整個停用排程器(cron 工具與 `/loop` 變不可用)。

### 1.2 subagent 在 headless 的語意

- 官方 headless 文件:背景 subagent 的結果屬於最終輸出的一部分,`claude -p`
  **會等它們完成**;v2.1.182 起等待上限預設 10 分鐘
  (`CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS` 可調)。
- 已知 bug(版本敏感):
  - [#56540](https://github.com/anthropics/claude-code/issues/56540) — 2.1.128/129
    在 cron/launchd 等**非 TTY 父行程**下,**平行** Task fan-out 會 hang 10 分鐘以上
    (單發正常);closed as not planned。本次 2.1.206 未重現(實驗 1)。
  - [#23909](https://github.com/anthropics/claude-code/issues/23909) — IDE 情境主執行緒
    在 subagent 跑完前提早結束。
- HIL 視角:headless 下權限請求**即時拒絕、不掛住等人**(實驗 2 第一輪實證,
  denial 進 result JSON 的 `permission_denials` 結構化欄位)——與本 repo
  permission matrix 實測、`arcp_poc/escalation.py` 升級迴路的前提一致。

### 1.3 codex 對照(GitHub 一手確認)

| 面向 | 現況 |
|---|---|
| `codex exec` | one-shot headless(stderr 進度/stdout 結果/exit code);有 `resume` 子命令;**無任何排程子命令**(本機 0.142.5 `--help` 實證,僅 `codex cloud` EXPERIMENTAL) |
| CLI 內建 cron | [openai/codex#22310](https://github.com/openai/codex/issues/22310) 社群提案(仿 Claude 的 cron_create/list/delete)**仍 open、無官方回應、無 PR** |
| 排程正解 | 三條路都在 CLI 之外:Mac app **Automations**(app 層 cron)、**Codex Cloud** 任務、**官方 GitHub Action** [`openai/codex-action@v1`](https://developers.openai.com/codex/github-action)(GitHub Actions `schedule:` 觸發 + 版本釘選 + 權限縮減) |
| 長時程 | `/goal` 模式(`~/.codex/config.toml` `[features] goals = true`):條件驅動自主 loop、預算制、pause/resume;互動 TUI 功能 |

**結論:codex 沒有 session 排程 → 沒有「靜默排程失效」這個風險面**;也沒有
內建背景 Bash 工具,等待=同步前景阻塞(實驗 4:300s 單一 tool call 完整等完)。
「功能少」對 harness 用途反而乾淨——缺的能力(排程/並行/HIL)本來就該由
harness 補(triggers/多票/HIL 層)。真正的能力差距是 **subagent**:codex 無原生
subagent fan-out,「一票內開多個 subagent」的工作型態只有 claude 能做——
這是 profile 選 engine 的準則之一,不是風險。

## 2. 實驗(步驟與結果;scratchpad 執行、不碰 repo)

三個實驗共同條件:從非 TTY 父行程呼叫、`--model haiku`、`--output-format json`。

### 實驗 1:`-p` 會不會等平行 subagent

```bash
claude -p --model haiku --output-format json \
  "Do not ask questions; act immediately. Use the Agent tool to spawn 2 subagents \
   IN PARALLEL (two Agent tool calls in the same message). Each subagent must run \
   the Bash command 'sleep 15' and then reply done. After BOTH subagents complete, \
   output exactly: SUBAGENTS_DONE"
```

結果:牆鐘 **29.9s**(> sleep 15,兩個 subagent 平行跑完才回)、result=
`SUBAGENTS_DONE`、`total_cost_usd`/`modelUsage` **含 subagent 用量**(單獨計
main 只有 118 output tokens,合計 1103)。→ **會等;費用可觀測**;非 TTY 無 hang
(2.1.206)。⚠️ 措辭注意:說 "Task tool" 會被理解成任務清單工具而反問(一回合
就浪費了),要說 **Agent tool** 或直接描述「開 N 個 subagent」。

### 實驗 2:背景 Bash 提早回傳後,背景工作的下場

```bash
claude -p --model haiku --output-format json --allowedTools=Bash \
  "... Run this Bash command AS A BACKGROUND TASK (run_in_background=true): \
   sh -c 'sleep 12; echo done > bg_done.txt'. Do NOT wait for it. \
   Immediately after starting it, output exactly: STARTED"
```

結果:回 `STARTED`,行程在主回覆後**多逗留 ~6.5s**(≈文件的 5 秒寬限)後退出;
退出當下與 15 秒後 `bg_done.txt` **都不存在**、無孤兒 `sleep` 進程。→ 背景工作
**隨行程被殺**,不是變孤兒繼續跑。副發現:同 prompt 在**預設權限**下跑,Bash 被
**即時拒絕**(`permission_denials` 有結構化記錄、行程正常結束)——headless 不會
掛住等核准。

### 實驗 3:`-p` 內建排程,行程退出後會不會觸發

```bash
claude -p --model haiku --output-format json --allowedTools=Bash,CronCreate \
  "... Use the CronCreate tool to schedule a ONE-TIME task 1 minute from now whose \
   prompt is: run bash to write the file cron_fired.txt ... \
   After creating it, output exactly: SCHEDULED <task-id>"
```

結果:07:07:34 建立、回 `SCHEDULED 9cdb0b72`(**工具呼叫成功、有 task id**);
應於 ~07:08:34 觸發;07:10:41 與 07:12+ 兩次檢查 `cron_fired.txt` **皆不存在**。
→ **排程隨行程死亡、靜默失效**:agent 誠實回報「已排程」,但那件事永遠不會發生,
而且**沒有任何錯誤訊號**。

### 實驗 4(codex 補測):5 分鐘 build,codex 怎麼等

```bash
codex exec --skip-git-repo-check --sandbox workspace-write \
  "Run exactly this command in the FOREGROUND and wait for it to finish \
   (do NOT background it): sh -c 'sleep 300; echo BUILD_OK > build_ok.txt'. \
   After it completes, read build_ok.txt and reply exactly: BUILD_RESULT: <content>"
```

結果:單一前景 exec tool call **`succeeded in 300015ms`**——整整 300 秒**沒有
timeout、沒有中斷、沒有重試**;隨後讀檔回 `BUILD_RESULT: BUILD_OK`,總牆鐘 5:21、
36,170 tokens。→ **codex 等長 build 的方式就是同步前景阻塞**,0.142.5 的 shell
工具在 300s 尺度沒有預設 timeout 上限;codex **沒有**內建背景 Bash 工具 →
沒有 claude 那種「丟背景提早回傳」的原生誘惑(功能少=風險面少)。

### 實驗 4b(codex 補測):等待期間事件流有無 keepalive

同型 prompt(sleep 90)+ `--json`,每行事件蓋時間戳:

```
08:38:57 item.started(command_execution)
   …(90 秒完全靜默,零輸出)…
08:40:27 item.completed
```

→ **長前景命令執行期間 `--json` 事件流完全靜默**。對 ARCP 的直接後果:
`stall_seconds`(預設 0=停用,`inner_runner.py`)若設成 < 最長單一命令時間,
**好好等 build 的 agent 會被 stall watchdog killpg 誤殺**——風險 R4 對 codex
已從推論升級為實測;claude 在長 Bash 執行期間同理(未另測)。

### 實驗 5(codex 補測):`codex exec resume` 可靠性(0.142.5 重驗)

repo 既有證據:2026-08-02 raw supervisor 2×2 崩潰矩陣(early/midtool ×
SIGTERM/SIGKILL)**全過**、thread id 從 `thread.started` 事後擷取來得及
(見 [crash-recovery](crash-recovery.md));rawcli 已接線。本次 0.142.5 重驗:

```bash
# ① 建 session 留暗號(--json 抓 thread_id)
codex exec --json --skip-git-repo-check --sandbox workspace-write \
  "Remember this codeword: MANGO42. Write it into note.txt. Reply exactly: OK"
# ② argv 坑驗證:resume 不吃 --sandbox
codex exec resume <id> --sandbox workspace-write "hi"
#    → error: unexpected argument '--sandbox' found(坑仍在;要用 -c sandbox_mode=)
# ③ 正式 resume(同目錄)→ 回 MANGO42 ✅
# ④ 從「另一個目錄」resume 同 thread → 仍回 MANGO42 ✅
```

→ **resume 可靠**:記憶跨行程回收;**session 不綁 cwd**(④ 補上
crash-recovery「workspace 搬家未對 codex 測」缺口的 session-store 半邊——
對照 claude session 綁啟動 cwd、搬家即死);argv 坑在 0.142.5 **仍在**,
rawcli 的 `-c sandbox_mode=` 繞法仍必要(`agent.py` `_build_command`)。
注意 `resume --last` 在多 session 併發下有 race,harness 應一律用顯式 id
(rawcli 現況即是)。另:非 git 目錄要 `--skip-git-repo-check`、非 TTY 要
stdin=DEVNULL——rawcli 兩者皆已處理。

### 實驗 6(claude 補測):全域 skill 漏入與 `--bare` / config-dir 隔離

動機:ARCP profile 自帶 skills/hooks(workspace 注入 `<ws>/.claude/skills|hooks`,
`workspace.py`),**不想**吃到跑 harness 那台機器的全域(user-level)skills。

**(a) 漏入實測**:在只放一個 `arcp-test-skill` 的 workspace 跑預設 `claude -p`
問 available skills → 回了 **46 個**:workspace 那 1 個 + 開發機全域的 45 個
(boris、superpowers 全家、obsidian、plugins…)。→ **漏入是全量的**,且
superpowers 這類「強制先呼叫 skill」的指令會直接改變 agent 行為;每次 attempt
還付全域 context 稅(本機觀測 cold call cache 讀 ~43k tokens)。

**(b) `--bare`**:help 全文=跳過 hooks/LSP/plugin/auto-memory/keychain 讀取/
CLAUDE.md 自動探索,**auth 嚴格限 `ANTHROPIC_API_KEY`(OAuth 與 keychain 一律
不讀)**;skills 僅能以 `/skill-name` 顯式解析。實測(訂閱 OAuth 登入、無 API
key):`claude -p --bare` → **`Not logged in`,直接不可用**。就算有 API key,
「skip hooks」也會把 **profile 注入的 workspace hooks 一起殺掉** → 對 ARCP
是過猛的工具,只適合「純機械任務 + API key 計費」的場景。

**(c) `CLAUDE_CONFIG_DIR` 受控 config dir**:三種嘗試皆 `Not logged in`
(空 dir / 複製 `.credentials.json`(macOS 不存在,憑證在 Keychain)/ 複製
`~/.claude.json`)→ OAuth 憑證解析**綁定 config dir**,訂閱登入下此路不通;
API key 環境下可行性高(未測)。

**結論與建議**:訂閱登入下,全域 skill 隔離**沒有乾淨的 CLI 開關**。務實解法
按序:① **部署衛生**——跑 poller 的機器用乾淨 HOME(bot 帳號,全域 `~/.claude`
不裝任何 skill/plugin);全域層本來就該視為部署資產。開發機跑測試時**心裡要有
漏入這回事**(46 個 skills 的行為擾動與 context 稅)。② 若改 API key 計費,
重測 (c) 受控 config dir(推測可行)或 (b) `--bare`+顯式加回。③ 追蹤官方
settings `skillOverrides` / permission `Skill(...)` deny 規則能否 pattern 式
隱藏 user-level skills(未驗證)。

## 3. 風險矩陣(對 ARCP)

| # | 風險 | 觸發條件 | 後果 | ARCP 現有防線 | 缺口 |
|---|---|---|---|---|---|
| R1 | agent 在 attempt 內建 session 排程(cron/loop)→ 靜默失效 | prompt/skill 誘導 agent「稍後檢查」 | agent 回報已安排,工作永不發生;**無錯誤可觀測** | verify 抓得到「該產出沒產出」→ FAILURE retry | 浪費 attempt;若 verify 沒覆蓋該產出則漏網 |
| R2 | 長 build 丟背景 + 提早回 done | agent 想「省時間」背景跑 build/測試 | build 被殺沒跑完;envelope 自稱 done | **證據型停止**:verify files/cmd 不過 → 不會誤判 SUCCESS | 同上,燒 attempt 與預算 |
| R3 | 平行 subagent fan-out 在非 TTY hang(版本敏感,#56540) | 特定 CLI 版本(2.1.128/129 已知) | attempt 卡住 10min+ | stall watchdog + `timeout_sec` + killpg(`start_new_session=True`)本來就會收掉 | 版本升級無冒煙會盲升 |
| R4 | 長前景命令/subagent 期間事件流靜默 → 誤觸 stall watchdog | `stall_seconds` ∈ (0, 最長單一命令時間);**codex 已實測**:等待 90s 零事件(實驗 4b),claude 同理推論 | 好好等 build 被 evict | 預設 `stall_seconds=0`(停用)不受害 | 設定指引:0 或 **> 最長單一前景命令時間**;長 build 的硬上限交給 `timeout_sec` |
| R5 | agent 建 **Cloud Routine** → 工作在 harness 視野外真的執行 | `/schedule` skill 可用且權限放行 | 無 trace/無預算/無 verify 的影子工作 | 無 | 需 deny(**未實測**,不宜真建雲端排程驗證) |
| R6 | HIL × subagent | — | **無風險**:HIL 在 harness 層(attempt 之間),attempt 內權限牆=即時拒絕+結構化記錄 | HIL 模型 + `escalation.py` | — |
| R7 | 全域(user-level)skills/plugins 漏入 attempt | 跑 harness 的機器 `~/.claude` 裝有 skills(開發機幾乎必然) | 行為擾動(如 superpowers 強制 skill 呼叫)+ 每 attempt context 稅(本機 ~43k tokens);profile 行為不可重現於他機 | workspace 注入的 skills/hooks 本身可控 | **訂閱登入下無乾淨 CLI 開關**(實驗 6);靠部署衛生:poller 機器用乾淨 HOME |

## 4. 建議修正(按性價比排序)

1. **一行環境變數,整類消除 R1**:起 `run_poller.py` 的環境設
   `CLAUDE_CODE_DISABLE_CRON=1`。rawcli 用 `subprocess.Popen` 直接繼承環境
   (`src/arcp/rawcli/agent.py` `run()`),所有派出的 `claude -p` 自動生效,
   **零程式改動**;要更保險可在 rawcli spawn 時顯式注入 env(小改動)。
2. **profile 模板/TICKET.md 明文禁令**(治 R2):「長時間 build/測試必須前景執行
   等到結束;禁止 run_in_background 跑交付相關工作;禁止建立任何排程或雲端任務」。
   等待的正確姿勢=前景 Bash/Monitor + `timeout_sec` ≥ 最長 build;超長工作拆
   兩個 attempt(首輪起 build 寫狀態檔、次輪 resume 驗收)——本來就是 ARCP
   resume 語意擅長的形狀。
3. **CLI 版本釘選 + 冒煙**(治 R3):凍結 snapshot 記錄 CLI 版本(本次 2.1.206 /
   0.142.5);升版前跑實驗 1 當 smoke(平行 subagent + 非 TTY)。
4. **subagent 型 profile 調參指引**(治 R4):`timeout_sec` 涵蓋 fan-out 牆鐘;
   `stall_seconds` 放寬;上線前用真 profile 驗一次 stall 是否誤判。
5. **deny 雲端排程**(治 R5):profile 權限層擋 `/schedule`/Routines 對應的
   skill/工具(`--disallowedTools` 或 permission deny 規則)。
6. (選)若未來想要「跑到條件滿足」的 attempt:Claude 的 `/goal` 官方支援
   `claude -p '/goal ...'` headless——它是把**單一回合拉長**(與 `-p` 相容),
   不是行程外排程;但與 ARCP 自身 verify-retry 迴路重疊,屬設計選擇非必需。

## 5. 誠實標註(哪些是實測、哪些是推論)

- ✅ 實測:實驗 1/2/3(claude 2.1.206)+ 實驗 4/4b/5(codex 0.142.5)+ 實驗 6
  (全域 skill 全量漏入、`--bare` OAuth 不可用、`CLAUDE_CONFIG_DIR` 三法皆
  Not logged in)全部於非 TTY 父行程;codex `--help` 無排程子命令;headless
  權限即時拒絕;codex 300s 前景等待、90s 事件流靜默、resume 暗號回收+跨目錄、
  `resume --sandbox` argv 坑仍在。
- ⚠️ 推論未實測:R4 的 claude 半邊(長 Bash 期間 stream 是否同樣靜默——codex
  已實測靜默,claude 同理推論);R5 Cloud Routine 逃逸
  (機制上成立,但不宜真建雲端排程驗證);#56540 的 hang 在 2.1.206 未重現,
  無法確認是否已修(issue 為 not planned 關閉,官方無修復記錄)。
- codex `/goal` 未實測(需 feature flag,且為互動 TUI 功能,非 exec 路徑)。

## 6. Sources

- [Claude Code 排程任務(官方,zh-TW)](https://code.claude.com/docs/zh-TW/scheduled-tasks)
- [Claude Code headless(官方)](https://code.claude.com/docs/en/headless)
- [anthropics/claude-code#56540 — 非 TTY 平行 Task fan-out hang](https://github.com/anthropics/claude-code/issues/56540)
- [anthropics/claude-code#23909 — subagent 跑完前主執行緒提早結束](https://github.com/anthropics/claude-code/issues/23909)
- [openai/codex#22310 — CLI durable cron 提案(open)](https://github.com/openai/codex/issues/22310)
- [Codex GitHub Action(官方文件)](https://developers.openai.com/codex/github-action)
- [pinggy — Claude /loop、/goal 與 Codex goal mode 比較](https://pinggy.io/blog/claude_code_loop_codex_goal_long_horizon_tasks/)
- [Codex CLI Automations 與排程](https://codex.danielvaughan.com/2026/03/27/codex-cli-automations-scheduled-tasks/)
- [Codex exec in CI](https://www.developersdigest.tech/blog/codex-exec-ci-headless-guide)
