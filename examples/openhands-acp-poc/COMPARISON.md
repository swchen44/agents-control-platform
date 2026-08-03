# A/B 實跑對照:raw supervisor vs OpenHands ACP(2026-08-03)

> 方法:**同一任務**(循序建檔 step1~5 內容鏈)、**同一判準**(路線 A 的
> `FileChecklistGrader`),同日同機實跑。數據源 `runtime_compare/results.json`
> (`compare_run.py` 產出;A 路另有 journal、B 路有 OpenHands 事件流可稽核)。
> 證據級別:標「實測」= 本輪實跑;標「歷史實測」= jira-agent-poc 先前矩陣;
> 標「分析」= 原始碼/文件推論(沿用 research v3 慣例)。

## 1. 量化結果(實測)

| | A-raw claude | B-OpenHands claude | A-raw codex | B-OpenHands codex |
|---|---|---|---|---|
| 結果 | ✅ done,grader PASS | ✅ done,grader PASS | ⛔ quota | ⛔ quota |
| 時長 | 30.4s | 31.6s | — | — |
| 事件數 | **248** | **14** | — | — |
| 事件粒度 | thinking delta 62、raw stream 162、tool 5+5 | ACPToolCallEvent 10、Message/Action/Observation | — | — |
| 成本 | $0.053(haiku,可控) | 未落地(冒煙 $0.45,adapter 預設模型) | — | — |
| session id | ✅ 預指定 | ✅(SDK 持久化) | — | — |

⛔ **codex 兩路皆被 ChatGPT 用量額度擋下**(`You've hit your usage limit... Aug 31st`)
——非相容性問題:A 路 codex 歷史實測 2×2 矩陣全過;B 路 codex-acp@1.1.2 **冒煙已
PASS**(68s、14 事件、file probe 過)。對照數據點待額度重置後補。
**營運發現(實測)**:訂閱 quota 是跨路線共用資源,兩條路都吃同一個 codex 帳號額度
→ pipeline 需要預算/節流管理。

## 2. 粒度判讀(實測)

同一任務,A 的原生流是 B 的 **~18 倍**事件量:

- **A(248 事件)**:含 token 級 thinking delta 與 stream_event——這是 watchdog
  「秒級 stall 偵測」與 token 計量的原料;代價是要自己 normalize(driver 層)。
- **B(14 事件)**:乾淨的語意層(工具呼叫/訊息/觀察),跨 agent 統一、不用自己寫
  driver;代價是**細粒度 watchdog 失去原料**——兩個工具呼叫之間若 agent 卡在
  thinking,B 路看不到心跳。

## 3. 質化對照

| 面向 | A raw | B OpenHands SDK(in-process) | 證據 |
|---|---|---|---|
| Setup | 零依賴,即開即用 | venv + SDK 重依賴 + npx adapter 預熱(首跑 3-4 分鐘,>SDK 90s timeout,會留半殘快取) | 實測 |
| Auth | CLI 本機登入 | **同樣吃本機登入,免 API key**(claude+codex 皆驗證) | 實測 |
| 模型控制 | `--model` 直接指定(haiku 省 8 倍) | `acp_model` 需額外設定,預設吃 adapter 預設 | 實測(A)/分析(B) |
| 終止語意 | 事件+exit code+grader(已釘陷阱) | Conversation.run() 返回 + ConversationErrorEvent(結構化錯誤,如 quota) | 實測 |
| Recovery | 三段梯度全實測(2×2×2 + workspace 搬家) | **`session/load` 重接已實跑**(2026-08-03,`resume_acp_test.py`):adapter SIGKILL 後 `acp_resume_session_id` 重接同 session(SDK `truly_resumed` 旗標 + 同 id,兩輪皆過);transcript 渲染器未接線 | 實測(兩路) |
| 控制窗口 | 事件+檔案系統雙觸發,kill 可精準投放(矩陣證明) | ⚠️ **中途干預無窗口**:adapter 把五步批次瞬間執行(step1 出現即殺仍五檔全在),外部看不到步驟間隙——粗粒度不只影響觀測,也影響控制 | 實測 |
| 錯誤面 | stderr 落 journal 自己判讀 | ConversationErrorEvent 帶 code/detail(quota 錯誤即由此精準捕獲) | 實測 |
| 依賴/版本面 | CLI schema 漂移(fixtures 回歸護欄) | SDK 1.39.1 + adapter pin(claude-agent-acp 已落後 npm 20 版)+ CLI 三層漂移 | 實測 |

## 4. 結論(維持 v3 §7 混合路線,新增實證)

1. **B 路線可行性已從「分析」升級為「實跑」**(claude 全綠;codex 冒煙綠、對照
   待 quota):SDK in-process headless 跑得動、auth 零設定、錯誤結構化。
2. **A 仍是一級公民的理由更具體了**:細粒度事件(watchdog 原料)、模型/成本可控、
   零依賴。B 的 14 事件語意層適合「要乾淨統一介面、不在乎秒級觀測」的場景。
3. **可插拔架構的正確性獲證**:同一 grader 判準跨 A/B 直接可用——差異化層
  (grader/recovery loop/escalation)確實獨立於 runtime 選擇。
4. **B 路 resume 已實跑(2026-08-03)**:adapter SIGKILL → `acp_resume_session_id`
   → session/load 重接同 session,續行完成(兩輪 4/4 判準)。誠實註記:兩輪 kill
   都落在任務實質完成後(批次執行無中途窗口),「任務中途續跑」語意由 A 路
   同一 claude session store 的 2×2 矩陣間接背書,非 B 路直接觀測。
5. 待補:codex 對照數據點(quota 重置後)、agent-server **行為驗證 spike**
   (讀碼部分已由使用者的行號級研究完成:`~/git/openhands/docs/research/
   openhands-acp-claude-code.md`,關鍵事實見 integration 分析 §3.5——
   多 workspace 併發原生、閒置 Evict→resume 常態化、bypassPermissions 一刀切、
   改造點座標 `_OpenHandsACPBridge` acp_agent.py:1041)。
   (B 路成本落地已撤——使用者決策 2026-08-03:A/B 未來同款 model,基準問題消失。)

## 5. 「把 A-raw 優點改造進 OpenHands」的可行性帳(2026-08-03)

| A-raw 優點(實測) | 移植到 OpenHands | 帳 |
|---|---|---|
| 模型/成本控制(haiku 省 8 倍) | ✅ 便宜可得:`acp_model` SDK 原生支援 | 幾行碼 |
| 差異化層(grader/recovery 迴路/escalation/transcript) | ✅ 已證可移植:不綁 runtime,同 grader 已跨 A/B 用 | 一兩天 |
| 錯誤結構化取證 | ✅ B 本來就好(`ConversationErrorEvent`) | 免費 |
| 細粒度觀測(248 vs 14,watchdog 心跳) | ⚠️ **結構性瓶頸**:細事件在 adapter 內部即丟棄,ACP 協定無承載欄位;要補只能 fork adapter(TS)並長期跟上游 | 高且持續 |
| 控制窗口(mid-task 精準 kill) | ⚠️ 同源:ACP 只有 turn 級 `session/cancel`,步驟間隙外部不可見 | 高且持續 |
| 零依賴 | ❌ 定義上不可得(SDK+adapter+CLI 三層版本鏈) | — |

**判讀**:便宜的(模型控制/差異化層)拿了就是;A 核心的細觀測/細控制卡在 **ACP 協定
資訊瓶頸**,fork adapter ≈ 回到自維護 driver 的老路且維護面更大(omnara 教訓)。
**合理形態是分工而非改造**:OpenHands 當可選 backend(語意層夠用的例行任務,
agent-server 形態另附併發/隔離);A-raw 留給需秒級觀測/精準控制的任務;差異化層
共用(已證可行)。**重算此帳的訊號**:ACP spec 出現 progress/細粒度 notification
擴充,或官方 adapter 開始轉發細事件。

## 6. A/B/C 三方對照(C.5,2026-08-03 實跑)

同任務(filechain)、同 grader、同機同日、claude haiku。route C = RawCLIAgent
(`harness/arcp_rawcli/`,OpenHands 骨架 + raw CLI 執行單元)。
數據源 `harness/runtime_abc/results.json`(`compare_abc.py`)。

| | A-raw supervisor | B-ACP(agent-server) | **C-RawCLIAgent** |
|---|---|---|---|
| 蒸餾事件(語意層) | 93(未蒸餾,含 token delta 噪音) | 17(ACP 粗語意) | **10(乾淨有意義)** |
| 原生保真(raw 行) | 93 | **0**(adapter 吞掉底層) | **94(全保留)** |
| cost(haiku) | $0.0285 | $0.0285 | $0.0291 |
| completed / grader | ✅ / ✅ | ✅ / ✅ | ✅ / ✅ |
| 中途控制窗口 | ✅(recovery_test kill) | ❌(批次無窗口) | ✅(C.4 fault kill) |
| crash→resume | ✅(2×2 矩陣) | ✅(session/load) | ✅(C.4,--resume) |
| setup | 零依賴 | venv+server(最重) | venv(無 server) |
| 骨架/可視化 | 自建 | OpenHands(GUI/detail) | **OpenHands(detail 兩視角)** |

**判讀:C 集大成。** A 的 93 ≈ C 的 94(同一 claude 原生流),但:
- **保真度**:C(94)≈ A(93)>> B(0)——B 的 adapter 把底層 CLI stream 吞掉,零保真。
- **語意乾淨度**:C(10 有意義)最可讀;A(93)混入 token delta 噪音、未蒸餾;
  B(17)粗且無底層。
- **只有 C 兩者兼得**:乾淨蒸餾語意層 + 原生全保真;A 只有原始無蒸餾,
  B 只有粗語意無保真。加上中途控制窗口(B 缺)、OpenHands 可視化(A 缺)。
- 成本三方近乎相同(同 model,使用者決策 A/B/C 同 model 後成本可比)。

**結論**:route C 把 A 的細粒度/控制/recovery 搬進 OpenHands 骨架,同時保有
B+ 的可視化/持久化,無 ACP 的保真損失與粗粒度。abc-roadmap §3 的「C 集大成」
從分析升級為實跑實證。

## 7. OpenHands agent-server 的併發價值(2026-08-03 實測)

回應「OpenHands 好處在小任務上感受不到」——併發是它相對 in-process/rawcli
真正買到的東西。`demo_concurrent.py`:**1 個 agent-server 進程(PID 7944)
同時管 4 個 conversation**,各自 workspace/事件流,grader 4/4 互不污染;
**總併發 wall-clock 37s ≈ 最慢單張,非 4× 串行**。

| | in-process / rawcli | agent-server |
|---|---|---|
| N 張票 | N 個獨立 CLI 子進程,各自為政 | 1 進程管 N conversation |
| wall-clock | 串行 ~N×(或自造併發) | 併發 ≈ 單張 |
| 生命週期 | 無 | 統一 + 閒置 evict→rehydrate |
| 隔離 | 各子進程 | 各 conversation 獨立 workspace |

**判讀**:任務越多、越要規模化(v5 D10 max_running 8),OpenHands 地基價值
越浮現;單張 trivial 任務用 rawcli 裸跑更省。這正是「架構讓你不用賭」——
profile 一行在 rawcli(輕、隔離、細粒度)與 openhands-server(併發、持久化、
可視化)間切換。**接進 harness dispatcher 的長駐共享 server(現每 attempt
自起)是 backlog。**

## 8. 執行形態:headless(stream-json) vs tmux-driven(交互 TUI)

> 動機:發現 OpenHands 依賴 libtmux,問「能否用 tmux 取代 claude -p/codex exec、
> 更有彈性、能連進去 debug」。先破誤解:libtmux 在 OpenHands 是給**自家
> CodeActAgent 的 bash 工具**做持久 shell(跨指令保 cwd/env、4-pane 並行),
> **Claude Code(ACP)根本不在 tmux 裡跑**(stdio 被 JSON-RPC 佔用、無 TTY)——
> 連 OpenHands 自己都刻意不把 Claude Code 放 tmux(讀碼研究 §4)。這事實本身
> 就是答案的一半:結構化輸出才是自動化基礎,tmux TUI 是給人看的。

| | headless(claude -p/codex exec,我們的路) | tmux-driven(交互 TUI,人可 attach) |
|---|---|---|
| 輸出 | 結構化 stream-json(事件/cost/session_id/result) | 終端 TUI(ANSI,給人看) |
| 可觀測性 | 細粒度事件→grader/狀態機/stall/detail page | 要 capture-pane 抓屏+解析 ANSI,**極脆弱** |
| 終止判定 | 明確 result 事件(is_error/cost) | 無機器可讀終止,靠抓屏猜 |
| resume | --session-id/--resume 明確(實測 4/4) | 交互模式 resume 語意模糊 |
| **人 attach 接管** | ❌ | ✅ **tmux 唯一真優點** |
| 持久跨 SSH 斷線 | 靠 store 續跑 | tmux session 活著 |
| 自動化 | 程式驅動、可靠 | send-keys+抓屏,時序/版本敏感 |

**判讀(致命問題)**:tmux 取代 headless = **失去結構化可觀測性**,而那是整個
harness 的地基——grader(證據型停止)、狀態機、stall 檢測、細粒度事件、detail
page、resume 全靠 stream-json。tmux 裡拿不到或靠脆弱抓屏(比 A 路教訓「不解析
transcript JSONL」更糟:那至少是 JSON,tmux 是 TUI 像素)。

**「連進去 debug」我們已有更好的**:detail page(💭思考/🔧工具/📋觀察 + 四層
trace + grader + cost + ticket 語義)比 tmux attach 更結構化、可讀、**唯讀安全**
(多人可看不誤觸;attach 可接管有風險)、5s 實時刷新。

**結論**:tmux **不該取代** headless(拆掉自動化地基),但可作為「需要人 attach
接管」的**可選 backend 並存**(profile 一行切換,符合可插拔架構),tradeoff 明確:
**自動化(headless:grader/stall/細粒度/自動 resume) vs 人工可接管(tmux)**。
多數 Jira 例行工單要自動化;極少數需人盯著操盤的可走 tmux。彈性/debug 需求已由
detail page 用結構化方式滿足——又一個「backend 可插拔、視角統一」的受益點。
