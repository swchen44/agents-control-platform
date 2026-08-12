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

**結論:codex 沒有 session 排程 → 沒有「靜默排程失效」這個風險面**;等待型工作
同樣必須前景做完才回傳。

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

## 3. 風險矩陣(對 ARCP)

| # | 風險 | 觸發條件 | 後果 | ARCP 現有防線 | 缺口 |
|---|---|---|---|---|---|
| R1 | agent 在 attempt 內建 session 排程(cron/loop)→ 靜默失效 | prompt/skill 誘導 agent「稍後檢查」 | agent 回報已安排,工作永不發生;**無錯誤可觀測** | verify 抓得到「該產出沒產出」→ FAILURE retry | 浪費 attempt;若 verify 沒覆蓋該產出則漏網 |
| R2 | 長 build 丟背景 + 提早回 done | agent 想「省時間」背景跑 build/測試 | build 被殺沒跑完;envelope 自稱 done | **證據型停止**:verify files/cmd 不過 → 不會誤判 SUCCESS | 同上,燒 attempt 與預算 |
| R3 | 平行 subagent fan-out 在非 TTY hang(版本敏感,#56540) | 特定 CLI 版本(2.1.128/129 已知) | attempt 卡住 10min+ | stall watchdog + `timeout_sec` + killpg(`start_new_session=True`)本來就會收掉 | 版本升級無冒煙會盲升 |
| R4 | subagent 型 profile 誤觸 stall watchdog | subagent 長時間安靜,主行程 stream 無新事件 | 好好跑著被 evict | —(推論,**未實測**) | `stall_seconds` 需放寬指引 |
| R5 | agent 建 **Cloud Routine** → 工作在 harness 視野外真的執行 | `/schedule` skill 可用且權限放行 | 無 trace/無預算/無 verify 的影子工作 | 無 | 需 deny(**未實測**,不宜真建雲端排程驗證) |
| R6 | HIL × subagent | — | **無風險**:HIL 在 harness 層(attempt 之間),attempt 內權限牆=即時拒絕+結構化記錄 | HIL 模型 + `escalation.py` | — |

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

- ✅ 實測:實驗 1/2/3 全部(2.1.206、非 TTY、haiku);codex `--help` 無排程子命令;
  headless 權限即時拒絕。
- ⚠️ 推論未實測:R4 stall 誤判(需真 profile 驗證);R5 Cloud Routine 逃逸
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
