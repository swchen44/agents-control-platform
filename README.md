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

## 資料流生命週期 / 狀態機(W10:HIL 模型)

搞定系統先搞定**資料流的生命週期**。一張 Jira 票在 harness 內部走 6 個 canonical 狀態
(W10 起改 **HIL(Human In the Loop)模型**):

```
待處理 ─路由·派工─▶ 進行中 ⇄ 排隊
 (todo)     │  (running)  (queued)
            ├─ 過程中需人 ─▶ HIL(Middle) ─ assignee→機器人·條件滿足 ─▶ 回 running
            │   (triage/審批/預算/交人)
            ├─ 完成/用盡attempts/UNKNOWN ─▶ HIL(End)  結果={成功|失敗|未定}
            │        │ 人評分(0–10)
            │        ├─(A)人續做→關票(closed,概念終點)
            │        └─(B)人判可續→native resume·重置額度→回 running
            └─ cancel/外部關Done/交接 ─▶ 撤銷(aborted)
```

（互動版:dashboard「概念」tab 有純 SVG 狀態機圖 + 6 態說明 + **模組架構圖/職責表**。）

**6 態**:待處理 / 進行中 / 排隊 / **HIL(Middle)** / **HIL(End)** / 撤銷。`closed` 是
概念終點(人關 Jira→離開 jql)。`success/failure/unknown` 是 **HIL(End) 的結果屬性**,
不再是頂層狀態。

- **HIL(Middle)**(過程中等人)= 舊「交人 inactive」+「等待人類 pending」合併;含開跑前
  的 triage/審批。resume 觸發 = `assignee` 改回機器人,harness 讀 description `human` 段
  重評條件(審批已填/預算已放寬/純交人無條件)滿足才續跑。
- **HIL(End)**(終點交人)= 跑完轉人評分,再由人 (A) 續做後關票、或 (B) 判可續 → native
  resume + 重置額度回「進行中」。

**狀態存在哪(重要)**:
- **Jira 這邊**:真正的 `status`(To Do/進行中/Done)存 Jira,harness 只讀進來鏡射到
  DB `ticket_watch.last_state`。
- **我們系統這邊**:內部判定 `outcome`(SUCCESS/FAILURE/ABORTED/UNKNOWN)+
  `pending_reason` 只存 DB `ticket_session`,**不寫回 Jira**;上面 6 態即由這些欄位
  (加 queued/inactive/有無 session)推導的單一 canonical 狀態(`canonical_state()`
  唯讀映射)。
- **harness 不主動 transition Jira 狀態**(只留言);關票=人做。成功/失敗後交人
  評分(人在 description 的 `human` 段填 `score: 0–10`),人填完再自己關票。
- **生命週期事件**都記在 journal `events.jsonl`(new_issue/attempt_*/resolved/pending/
  handoff/jira_write/human_score…);ticket 詳情頁的**事件時間軸**由它繪製。

> 完整分層模組架構、職責表、以及 **agent↔agent 交接(同票 `next` vs 跨票 `base` 怎麼選)**
> 見 [docs/design/architecture.md](docs/design/architecture.md)。生命週期細節見
> [docs/design/lifecycle.md](docs/design/lifecycle.md)。
> ⚠️ HIL **行為**(W10.2)與 **a2a base 交接**(W10.3)為目標設計,實作暫緩、待審。

## 多實例部署(同一台機器並存多個 Control Plane)

想同時跑多個 Control Plane(例:一個顧 SCRUM、一個顧 OPS),做法是**複製整個
`agents-control-platform` 資料夾**成獨立一份,各自有獨立 `runtime_live/`、設定與 port。
每個實例在 dashboard 左上角會顯示 `ARCP Control Plane · <name>` 方便分辨。

**每個實例務必各自不同的(否則會互相干擾):**

| 項目 | 在哪設 | 為何 |
|---|---|---|
| **實例名 `name`** | `routes.yaml` → `outer_loop.source.name`(或 env `ARCP_NAME`) | 顯示在 dashboard 標題/瀏覽器分頁,分辨是哪個實例 |
| **Jira project + jql**(最重要) | `routes.yaml` → `source.project` / `source.jql` | ⚠️ **兩個實例絕不可 poll 同一 project/重疊 jql** —— 否則兩個 poller 互搶同一批票、覆寫彼此狀態(這正是我們 e2e 併發時撞到的 flaky 來源)。用不同 project,或至少用不重疊的 label/JQL 濾條 |
| **control API port** | `routes.yaml` → `control.port`(預設 8787) | 每實例的 REST 控制面要獨立 port |
| **dashboard port** | 啟動 `detail_server.py <runtime> <port>` 的 `<port>`(預設 8788) | 每實例的 dashboard 要獨立 port |
| **dashboard→control 指向** | `detail_server.py` 第三引數或 env `ARCP_CONTROL_URL` | dashboard 的 Evict/狀態按鈕要打到**自己這個**實例的 control port,不能指到別台 |

**共用、但要留意的:**

- **Jira 憑證** `~/.env`(`JIRA_BASE_URL/EMAIL/API_TOKEN`):同一個 Jira 站的不同
  project 可共用同一份;若要接**不同 Jira 站**,需為該實例準備不同憑證來源
  (目前 `config.jira_credentials` 固定讀 `~/.env`,跨站需自行調整 env path)。
- **機器人帳號 `bot_account_id`**:同站同 bot 帳號在**不同 project** 上並存 OK(自家
  `[agent]` 留言互相忽略);但若不慎讓兩實例落到**同一 project**,一方會把另一方的
  assignee/留言當「外部變更」處理 → 再次強調:**分 project**。
- **claude / codex 登入**(`~/.claude`、`~/.codex`)全域共用:沒問題,但兩實例的
  agent 併發會共用同一組 API rate limit 與**花費**。預算上限(單次/月)是
  **per-instance**(各讀自己的 journal),**跨實例總花費不會合計** → 併發時把每實例的
  `concurrency` 設保守一點,避免合計超過機器/額度。
- **agent session / transcript 檔**(`~/.claude/projects`、`~/.codex/sessions`)全域:
  session id 唯一不衝突;transcript 與月花費彙總各讀自己實例的 journal,per-instance OK。
- **dashboard 綁定**:預設 `0.0.0.0`(內網開放)。多實例只要 port 不同即可並存;要鎖
  本機用 `ARCP_DASH_HOST=127.0.0.1`。

**快速範例(起第二個實例 "ops"):**

```bash
cp -R agents-control-platform arcp-ops && cd arcp-ops/harness
# 編輯 routes.yaml:source.name: ops、source.project/jql 改成別的專案、control.port: 8797
python3 run_poller.py &                                  # 用 routes.yaml 的 control.port
ARCP_DASH_HOST=127.0.0.1 python3 detail_server.py ./runtime_live 8798 \
  http://127.0.0.1:8797                                  # dashboard 8798 → 指向自己的 control 8797
```

> 一句話:**分資料夾、分 name、分 Jira project/jql、分 port(control + dashboard)、
> dashboard 指向自己的 control**。其餘(憑證/登入/session 檔)可共用,但預算與機器
> 資源是 per-instance、不跨實例合計,併發請設保守。

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
- [x] **Jira 驅動 harness(B):outer/inner loop、三態 outcome、指令通道、
      agent-server + 視覺化(detail page)**(`harness/`,M1-M4;真 Jira 端到端)
- [x] **RawCLIAgent(路線 C):OpenHands 骨架 + raw CLI,不 fork;三方對照 C 集大成
      (保真≈A、語意乾淨勝 B、控制窗口/可視化兼得)**(`harness/arcp_rawcli/`,C.0-C.6)
- [x] waiting-permission → Jira ticket 升級迴路:denial 事件驅動開票 + 結果回寫
      (含結構化 permission_denials 與 resume 指令,`arcp_poc/escalation.py`)

## License

尚未定(研究階段)。引用或試用歡迎開 issue 交流。
