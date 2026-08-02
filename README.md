# ARCP — Agent Runtime / Control Plane

讓 `claude -p`、`codex exec` 這類 **headless coding agent** 能長時間可靠執行、
**可觀測(trace)**、**可控制(control)**,並由 **Jira 事件驅動**。

> Make headless coding agents (Claude Code / Codex CLI) long-running, traceable,
> controllable, and Jira-event-driven — with every claim pinned by real experiments,
> not documentation folklore.

## 這個專案解決什麼

headless 模式下的 coding agent CLI 有共同缺口:

- 跑一半掛了(crash、SSH 斷線、機器重啟)→ 進度全失,**沒有跨 CLI 一致的 recovery**
- 事件流各說各話(schema、終止語意都不同)→ **沒有統一的 trace / 狀態機**
- 卡住(等 permission、API hang)沒人知道 → **沒有 stall 偵測與升級迴路**
- agent 說「做完了」不可信 → **需要證據型停止**,不是自稱完成

ARCP 的路線:**自寫輕量 supervisor(raw subprocess)為一級公民**,OpenHands ACP
留作可插拔對照後端,只在差異化層(跨 CLI recovery、git checkpoint 語意)投入原創。

## 內容物

| 路徑 | 內容 |
|---|---|
| `research/` | 研究報告(v3 最新):需求規格、開工級設計、三路線對照、**全部實測釘死的事實** |
| `examples/jira-agent-poc/` | **可跑 PoC**(~800 行、零依賴):Jira watcher → rule 引擎 → workspace+skills → 監督 CLI → 統一 trace |
| `examples/jira-agent-poc/recovery_test.py` | **crash→resume 矩陣實驗 harness**:受控 kill × 信號矩陣 + 確定性判準 |
| `examples/openhands-acp-poc/` | **路線 B PoC**:OpenHands SDK 包 claude/codex headless(ACP adapter),含 A/B 實跑對照 `COMPARISON.md` |
| `examples/jira-agent-poc/fixtures/` | claude / codex 的**真實事件流**(含 crash+resume 黃金樣本對),協定回歸測試用 |
| `HANDOFF.md` | 零上下文接手文件:已敲定決策、實測事實、下一步清單 |

## 已用實測釘死的事實(精選)

**Crash recovery 可行,且兩家 CLI 的路徑不同:**

- `claude -p --session-id <uuid>` 可**預先指定** session id → crash 後
  `--resume <id>` 重接。2×2 矩陣(思考中/工具執行中 × SIGTERM/SIGKILL)**4/4 PASS**:
  同 session、記得進度、**不重工**(crash 前檔案 mtime 不變)、任務補完。
- codex **不能預指定**,但從 `thread.started` 事件**事後擷取** thread id 來得及 →
  `codex exec resume <id>` 重接成功(2×2 矩陣全時機實證,含最嚴苛的「工具執行中 SIGKILL」)。

**過程踩出來的陷阱(每一條都有事件流佐證):**

1. codex 收 SIGTERM **優雅退場 rc=0** → 「事件 OR exit code」判據會把砍到一半的
   run 誤判成完成。**exit code 只證明程序結束,不證明任務完成** → 證據型 grader 是必要品。
2. `codex exec resume` **不接受 `--sandbox`**(rc=2),要 `-c sandbox_mode="..."`。
3. kill 必須殺 **process group**:只殺 CLI pid,codex 的 shell 子程序會孤兒續跑,
   在 supervisor 背後把任務偷偷做完。
4. codex 工具粒度不可預期(同 prompt 有時單指令打包、有時逐步)→
   **事件流不能當進度真值,要以檔案系統真值判讀**。
5. 兩家終止語意不對稱:claude 有明確 `result` 事件;codex 靠 `turn.completed` + exit。
6. 筆電**系統睡眠會凍結 supervisor 計時器**,產生假 stall / 假 hang —— live 監督
   要防睡或跑在 server 上。

**ACP 對照(原始碼查證):** ACP 有 `session/load` resume 語意,但底層耐久性與
raw 同源(adapter 委託 CLI 自家 session 檔);OpenHands 另有「事件史注入新 session」
的 bootstrap-prompt resume 設計,值得抄作 raw 路徑的降級層。詳見 research v3 §6.4。

## 快速開始

```bash
cd examples/jira-agent-poc

# 離線、免 token:真實事件流跑過 normalize → 狀態機 → trace
python3 replay_demo.py

# 免 token:7 項 self-test(rule 引擎 + 事件正規化 + 狀態機)
python3 selftest.py

# live(花 token):真實 claude -p / codex exec 全流程
python3 run_demo.py claude
python3 run_demo.py codex "Reply with exactly the word: pong"

# live(~$0.2):crash→resume 2×2 矩陣 + 確定性判分(建議 caffeinate 防睡眠)
python3 recovery_test.py            # claude
python3 recovery_test.py --agent codex
```

## 架構一眼看

```
Jira issue ─▶ rule engine(assignee/keyword JSON)─▶ workspace + skills 裝配
        ─▶ supervisor spawn(claude -p | codex exec)
        ─▶ driver 正規化 ─▶ 統一 AgentEvent ─▶ 狀態機 ─▶ journal(events.jsonl + snapshot)
                                   │
                     watchdog(stall)· control(pause/kill/resume)· crash→resume
```

## 現況與路線圖

研究階段(pre-alpha),介面會變。已完成:統一 event schema、雙 CLI driver、
supervisor(live+replay)、rules 引擎、Jira watcher、crash→resume 基線實測。

進行中 / 下一步(節錄自 research v3 §9.3):

- [x] **證據型停止**:確定性 grader 決定 DONE,證據不過覆寫 FAILED(`arcp_poc/grader.py`)
- [x] journal → transcript 降級 resume:session store 遺失時從 journal 渲染
      transcript 開新 session 續跑,live 驗證不重工(`--resume-mode transcript`)
- [x] Claude permission 行為矩陣:6 mode × 雙探針實測——headless 下拒絕即時、
      **沒有 mode 會掛住等核准**;acceptEdits 實際範圍比名稱寬(`permission_matrix.py`)
- [x] **自動 recovery 迴路**:run → grade → 梯度 resume,live 驗證硬 crash 與
      rc=0 假完成皆自動修復(`arcp_poc/recovery_loop.py` + `loop_demo.py`)
- [x] workspace 搬家情境 resume(#48835 一般形式):claude session 綁啟動 cwd,
      搬家後原生 resume 必死——transcript 降級救回不重工(`workspace_recovery_test.py`)
- [x] OpenHands ACP 對照(路線 B):SDK in-process headless 跑通、本機登入免 key、
      同任務同 grader 對照 A 248 vs B 14 事件(`examples/openhands-acp-poc/`)
- [x] waiting-permission → Jira ticket 升級迴路:denial 事件驅動開票 + 結果回寫
      (含結構化 permission_denials 與 resume 指令,`arcp_poc/escalation.py`)

## License

尚未定(研究階段)。引用或試用歡迎開 issue 交流。
