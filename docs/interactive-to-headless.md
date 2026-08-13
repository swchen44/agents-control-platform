# 從互動 Claude Code 到 headless — 把你的用法搬進 ARCP

> 對象:已經在終端機**互動**用 Claude Code / Codex 用得順手,現在要把同樣的工作
> 交給 ARCP(headless `claude -p` / `codex exec`)跑的人。核心觀念:**真正需要
> 重驗證的是 interactive→headless 這一步**,不是個別開關——互動模式的安全與便利
> 大半靠「人在旁邊」,headless 把人拿掉後,剩下的只有**確定性防線**(allowlist /
> deny / sandbox / verify)。遷移=把你腦中的判斷改寫成 profile 設定與驗證規則。
> 事實基礎:[headless 排程/subagent 研究](research/2026-08-headless-scheduling-subagents.md)
> 的 6 個實驗 + 官方文件 + 社群文章(§5)。2026-08-11。

## 1. 差異總表(互動 vs headless)

| 面向 | 互動 session | headless `claude -p`(= ARCP attempt) |
|---|---|---|
| 權限請求 | 跳出來問你,你按 yes/no | **即時拒絕、不掛住**;denial 進 result JSON `permission_denials`(實測) |
| 排程(`/loop`、CronCreate) | session 活著就會執行 | 建立「成功」回 task id,但**行程退出即靜默死亡、永不執行**(實測) |
| 背景 Bash(`run_in_background`) | 跑完通知你 | 主回覆後 **~5s 寬限即被殺**,不留孤兒(實測) |
| subagent(Agent tool) | 即看即等 | **會等全部跑完**才回傳(上限預設 10 分鐘,`CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS`);費用合併進 `total_cost_usd`(實測;平行 fan-out 有版本敏感 hang 前科 [#56540](https://github.com/anthropics/claude-code/issues/56540)) |
| 中途修正 | 隨時打字、Esc 中斷、rewind | 不存在;只能靠 harness 層的 verify-retry / evict / HIL |
| `/config` `/login` 等內建命令 | 可用 | 不可用 |
| CLAUDE.md / skills / MCP / hooks | 全自動載入 | **預設也全載入**——cold call 可達 ~15 萬 tokens;`--bare` 全關、再用旗標明確加回(官方預告 `--bare` 未來可能成 `-p` 預設) |
| claude.ai connector MCP(Slack 等) | 可用 | **不載入**(只有本機 stdio/HTTP MCP,[#36833](https://github.com/anthropics/claude-code/issues/36833)) |
| 成本可視 | `/cost` | `--output-format json` 的 `total_cost_usd` |
| session 續接 | 同視窗繼續 | `--resume <id>`;**session 綁啟動 cwd**(搬家即死,見 [crash-recovery](research/crash-recovery.md)) |

**codex 對照**:`codex exec` 無排程、無背景 Bash 工具——等待=同步前景阻塞
(實測 300s 單一 tool call 完整等完);`exec resume` 可靠且 **session 不綁 cwd**;
無原生 subagent。「功能少=風險面少」,缺的能力由 harness 補。

## 2. 遷移步驟(checklist)

1. **把「你會答應什麼」寫成 allowlist。** 互動時你腦中那套「這個可以按 yes」→
   `--allowedTools`(支援 pattern 如 `Bash(git diff *)`)+ `--permission-mode`;
   破壞性操作(push/rm/curl)用 deny 規則硬擋。ARCP 對應:profile 的
   `agent.permission_mode` / `allowed_tools`(rawcli 已透傳)。
   ❌ **不要**把互動流程直接加 `--dangerously-skip-permissions` 了事——那是把
   五層人肉防線換成零層。
2. **把「你會看 diff 判斷好壞」寫成 verify。** 互動時你看結果決定收不收 →
   profile `verify`(files/cmd/json)。這就是 ARCP 證據型停止:agent 自稱 done
   不算數,證據過了才 SUCCESS。
3. **關掉在 `-p` 裡註定失效的功能。** `CLAUDE_CODE_DISABLE_CRON=1`(per-spawn
   注入或 poller 啟動環境;**別放全域 shell profile**——那會關掉你自己互動
   session 的排程)。`=1/=0` 對成功結果**無差別**(session 排程在 `-p` 本來就
   永不執行),關掉只是把「假成功陷阱」換成「誠實不可用」→ **不需要為此重驗證
   既有 profile**。實測探針:設了之後 agent 回 `CRON_UNAVAILABLE`。
4. **長工作前景等。** build/測試在前景跑完再回報;`timeout_sec` ≥ 最長時間;
   TICKET.md 明文禁 `run_in_background` 跑交付相關工作。`stall_seconds` 設
   0(預設)或 **> 最長單一命令時間**——長前景命令執行期間事件流完全靜默
   (codex 實測 90s 零事件),設太緊會誤殺好好等 build 的 agent。
5. **決定 context 載入策略。** `-p` 預設全載入——實測在裝了 45 個全域 skills
   的開發機上,workspace 裡的 attempt 會**全量吃到**它們(行為擾動 + context
   稅)。⚠️ `--bare` 不是解法:它 **不讀 OAuth/keychain**(訂閱登入直接
   `Not logged in`,實測),且 skip hooks 連 profile 注入的 workspace hooks
   一起殺;`CLAUDE_CONFIG_DIR` 受控目錄在訂閱登入下也打不通(實測)。
   **務實解法=部署衛生**:跑 poller 的機器用乾淨 HOME(全域不裝 skill/
   plugin);API key 計費環境才考慮 `--bare`+明確加回。細節見
   [研究報告實驗 6](research/2026-08-headless-scheduling-subagents.md)。
6. **輸出走契約。** `--output-format json`(單發)或 stream-json(ARCP 現況),
   `--json-schema` 強制 envelope;監控 `total_cost_usd` 可偵測 context 膨脹。
7. **冒煙驗證**(每搬一個 workflow、每升一次 CLI 版都跑一輪):
   - **權限探針**:預期 denial 即時、不掛住、`permission_denials` 有記錄;
   - **subagent 探針**:平行 fan-out 於非 TTY 父行程下等完不 hang(#56540 前科);
   - **排程探針**(若設 DISABLE_CRON):agent 回報工具不可用;
   - **代表性任務**跑一輪,verify 通過。

## 3. 教訓與注意事項

- **防線清點**:互動的五層人肉防線(權限提示/rewind/看 diff/手動中斷/計畫審批)
  在 headless **同時消失**;倖存的只有確定性防線。社群數據:約束違規率
  IDE ~32% → CLI ~49%。「juniors trust the prompt, seniors engineer the
  environment.」
- **exit code / 事件流 / agent 自稱 done 都不可信**——SIGTERM rc=0 假完成、
  事件粒度變異都有實測前科([crash-recovery](research/crash-recovery.md));
  進度與完成的真值在**檔案系統/工作區**。
- **「稍後檢查」型 workflow 在 `-p` 不成立**(如每 10 分鐘盯 CI):不是開關
  問題,要改寫成 harness 語彙——ARCP 的 triggers job(排程)、verify-retry
  (重試)、HIL(要人)。
- **憑證**:headless 靠登入 token,過期即死、無自動刷新;長跑部署要納入
  憑證管理。
- **選 engine 準則**:要 subagent fan-out → claude;要跨 cwd resume 韌性 →
  codex;兩者的排程/背景風險面 codex 天然較小。
- **版本釘選**:headless 行為版本敏感(#56540 hang、`--bare` 預設化預告);
  凍結 snapshot 記錄 CLI 版本,升版跑 §2-7 冒煙。

## 4. ARCP profile 對應速查

| 互動時你在做的事 | ARCP 對應 |
|---|---|
| 按 yes/no 核准工具 | profile `agent.permission_mode` + `allowed_tools` |
| 看 diff 收貨 | profile `verify`(files/cmd/json)+ grader |
| 等 build 跑完再繼續 | 前景執行 + `agent.timeout_sec`;`stall_seconds`=0 或 >最長命令 |
| 開 subagent 分工 | claude profile + `timeout_sec` 涵蓋 fan-out;stall 放寬 |
| `/loop` 盯狀態 | `outer_loop.triggers`(script-job/agent-job) |
| 出錯了打字糾正 | verify 失敗證據餵回 resume(自動)/ HIL 表單(要人) |
| `/cost` 看花費 | envelope cost + budget 六層上限(見 [budget](design/budget.md)) |

## 5. Sources

- [研究報告:headless CLI × 排程/subagent 風險(本 repo,6 實驗)](research/2026-08-headless-scheduling-subagents.md)
- [Claude Code headless(官方)](https://code.claude.com/docs/en/headless) ·
  [排程任務(官方)](https://code.claude.com/docs/zh-TW/scheduled-tasks)
- [claude -p 到底載入什麼、何時該用 --bare(dev.to)](https://dev.to/rulestack/claude-p-what-headless-claude-code-actually-loads-and-when-bare-is-the-right-call-182c)
- [Headless 時人肉防線全滅,hooks 是僅存防線(ranjankumar)](https://ranjankumar.in/claude-code-headless-hooks-human-backstop)
- [Claude Code Headless Self-Hosting Guide(amux)](https://amux.io/guides/claude-code-headless/)
- [CI/CD and Headless Mode(Angelo Lima)](https://angelo-lima.fr/en/claude-code-cicd-headless-en/)
- [#36833 connector MCP 不載入](https://github.com/anthropics/claude-code/issues/36833) ·
  [#56540 非 TTY 平行 fan-out hang](https://github.com/anthropics/claude-code/issues/56540)
