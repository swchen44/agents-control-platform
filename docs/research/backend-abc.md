# 後端路線對照:A(raw)/ B(OpenHands-ACP)/ C(rawcli)

> **一句話結論**:三條路線同任務、同 grader、同機實跑後,**C(rawcli)集大成勝出** —— 原生保真度追平 A(94 vs 93 raw 行)、語意層比誰都乾淨(10 條有意義事件)、保有 A 的中途控制窗口與 B 的可視化/持久化,同時甩掉 ACP 協定的保真損失與粗粒度。A 留作對照參考,B 是 short-term 過渡。

## 研究問題(三條路線各解決什麼)

讓 `claude -p` / `codex exec` 這類 headless coding agent 能**長時間可靠執行、可 trace、可 control**,由 Jira 事件驅動。同一目標有三種後端形態,各自押在不同取捨上:

- **A** 問:自己寫 supervisor,能不能拿到最細的觀測與最精準的控制?(能,但要重造 OpenHands 走過的坑)
- **B** 問:借 OpenHands SDK + ACP adapter,能不能最快跑通、白撿可視化與併發?(能,但吃 ACP 協定的粗粒度與保真損失)
- **C** 問:把 A 的細觀測/細控制搬進 OpenHands 骨架,能不能兩者兼得?(能,已實跑實證)

## 三路線是什麼

| 路線 | 定義 | 角色 |
|---|---|---|
| **A — raw supervisor** | 自寫 supervisor(`examples/jira-agent-poc/`),直接 spawn CLI、解析原生 stream-json | 對照組 / 參考實作,不下場當產品 |
| **B — OpenHands-ACP** | OpenHands SDK + ACP adapter 包 claude/codex headless;鏈為 `ACPAgent → adapter 子行程(node)→ 內嵌 Claude Code` | short term,最快可執行 |
| **C — rawcli(RawCLIAgent)** | 在 OpenHands 骨架內寫的 Agent 實作(`src/arcp/rawcli/`),**不走 ACP**,直接 spawn CLI、解析原生 stream-json、發完整細粒度事件 | long term 主線 |

C 的關鍵澄清:細粒度瓶頸實測定位在 **ACP 協定 + adapter**(細事件在 adapter 內部即丟棄,OpenHands 側橋 `_OpenHandsACPBridge` acp_agent.py:1041 只收四類通知)。fork adapter(TS)是劣路(永遠跟上游、協定無承載欄位),故 C 選擇繞開 ACP,搬運 A 期已付清的困難知識(schema/終止語意/resume 梯度/陷阱清單)。

## 實測對照(表格 + 關鍵數據)

### A vs B 頭對頭(2026-08-03,同任務 filechain、A 的 `FileChecklistGrader`、claude)

| | A-raw claude | B-OpenHands claude |
|---|---|---|
| 結果 | ✅ done,grader PASS | ✅ done,grader PASS |
| 時長 | 30.4s | 31.6s |
| 事件數 | **248** | **14** |
| 事件粒度 | thinking delta 62、raw stream 162、tool 5+5 | ACPToolCallEvent 10、Message/Action/Observation |
| 成本 | $0.053(haiku,可控) | 未落地(冒煙 $0.45,adapter 預設模型) |

同一任務,A 的原生流是 B 的 **~18 倍**事件量。A 的 248 條含 token 級 thinking delta —— 這是 watchdog「秒級 stall 偵測」與 token 計量的原料;B 的 14 條是乾淨語意層,但**兩個工具呼叫之間 agent 卡在 thinking 時,B 看不到心跳**。

### A / B / C 三方對照(2026-08-03,C.5 實跑;同任務、同 grader、claude haiku)

| 維度 | A-raw supervisor | B-ACP(agent-server) | **C-RawCLIAgent** |
|---|---|---|---|
| 蒸餾事件(語意層) | 93(未蒸餾,含 token delta 噪音) | 17(ACP 粗語意) | **10(乾淨有意義)** |
| 原生保真(raw 行) | 93 | **0**(adapter 吞掉底層) | **94(全保留)** |
| cost(haiku) | $0.0285 | $0.0285 | $0.0291 |
| completed / grader | ✅ / ✅ | ✅ / ✅ | ✅ / ✅ |
| 中途控制窗口 | ✅(recovery kill) | ❌(批次無窗口) | ✅(fault kill) |
| crash→resume | ✅(2×2 矩陣) | ✅(session/load) | ✅(--resume) |
| setup | 零依賴 | venv + server(最重) | venv(無 server)※ |
| 骨架 / 可視化 | 自建 | OpenHands(GUI/detail) | **OpenHands(detail 兩視角)** |
| 語意乾淨度 | 混 token delta 噪音、未蒸餾 | 粗且無底層 | **最可讀** |

※ C 的 setup 為 C.5 對照當時形態;後續 W5.5 已使 rawcli 脫離 OpenHands 依賴(見末節)。

**判讀:C 集大成。**
- **保真度**:C(94)≈ A(93)>> B(0) —— B 的 adapter 把底層 CLI stream 吞掉,零保真。
- **語意乾淨度**:C(10 有意義)最可讀;A(93)混入 token delta 噪音、未蒸餾;B(17)粗且無底層。
- **只有 C 兩者兼得**:乾淨蒸餾語意層 + 原生全保真,再加上中途控制窗口(B 缺)與 OpenHands 可視化(A 缺)。
- 成本三方近乎相同(使用者已決策 A/B/C 同 model,成本基準問題消失)。

### 綜合維度速查(質化)

| 維度 | A | B | C |
|---|---|---|---|
| 保真度(raw) | 高(93) | 無(0) | **高(94)** |
| 語意乾淨度 | 低(噪音) | 中(粗) | **高(10)** |
| 控制窗口(mid-task kill) | ✅ | ❌(turn 級 `session/cancel`) | ✅ |
| 隔離粒度 | 各 CLI 子進程 | 各 conversation 獨立 workspace/worktree | 各 CLI 子進程 |
| 依賴鏈 | 零依賴(CLI schema 漂移由 fixtures 護欄) | SDK 1.39.1 + adapter pin + CLI 三層漂移 | venv → 後 W5.5 純 stdlib |
| 啟動延遲 | 即開即用 | adapter npx 首跑 3-4 分鐘(> SDK 90s timeout) | 無 adapter 預熱 |
| 併發 | 無(N 子進程各自為政) | agent-server:1 進程管 N conversation | 無(裸跑)/ 可掛 server |

## 各路線的坑(OpenHands / ACP 陷阱實錄)

B 路(OpenHands-ACP)在落地過程踩到的真實陷阱,是 C 選擇繞開 ACP 的直接證據:

- **litellm rust-wheel 建不起來**:最新 litellm 是 python/rust 混合包,本機 rustc 差一版 → 依 OpenHands 自家 uv.lock 鎖 `litellm==1.93.0` 純 wheel 才解。
- **90s startup timeout < npx 首跑**:SDK 硬限 startup 90s,但 adapter npx 首跑要下載 3-4 分鐘,中斷還會留半殘 npx 快取造成 enoent → 需先 `npx -y <pkg> --version` 預熱,半殘快取清 `~/.npm/_npx/<hash>` 才復原。
- **批次無 kill 窗口**:adapter 把五步批次瞬間執行(step1 出現即殺,仍五檔全在),外部看不到步驟間隙 —— **粗粒度不只影響觀測,也影響控制**。ACP 只有 turn 級 `session/cancel`。
- **裝 agent-server 反噬 SDK 版本**:裝 agent-server 會從 PyPI 拉舊 `openhands-sdk 1.20.0` 覆蓋 editable 1.39.1 → acp_models 消失、ACP 全壞。修法:四個第一方包一律 `pip install --no-deps -e`。
- **隱藏執行期依賴**:agent-server 額外要手動裝 `libtmux`(editable 沒帶)。
- **adapter 版本落後**:`claude-agent-acp` 已落後 npm 20 版;pinned `@0.44.0`。細事件補不回來 —— 要補只能 fork adapter(TS)長期跟上游(omnara 教訓)。
- **成本失控預設**:B 冒煙 $0.45 吃 adapter 預設模型;A 用 `--model haiku` 省約 8 倍($0.053)。`acp_model` 雖 SDK 原生可設,但預設不省。
- **生命週期硬限**(OpenHands 讀碼研究):startup 90s 硬限、prompt 1800s idle 限、閒置 20 分鐘 Evict(子行程關閉、狀態存 base_state.json)→ 再存取 rehydrate + `load_session` resume;`bypassPermissions` 一刀切關掉權限詢問(治理押 workspace 隔離)。

「把 A-raw 優點改造進 OpenHands」的帳:便宜的(模型控制、差異化層)拿了就是,但 A 核心的**細觀測/細控制卡在 ACP 協定資訊瓶頸**,fork adapter ≈ 回到自維護 driver 的老路且維護面更大。合理形態是分工而非改造 —— 這正是 C 的立論。

## 結論:主線選 C(rawcli)的理由

1. **保真無損**:C 繞開 ACP,直接吃 CLI 原生 stream-json,94 raw 行 ≈ A 的 93,而 B 是 0。細事件是 watchdog / stall / token 計量的原料,不能丟。
2. **語意最乾淨**:C 蒸餾出 10 條有意義事件,比 A 的未蒸餾噪音、B 的粗語意都可讀。
3. **控制窗口回來了**:C 有中途 fault kill(B 因批次執行結構性缺此窗口)。
4. **可視化白撿**:C 活在 OpenHands 骨架內,detail 兩視角、GUI、持久化照用(A 得自建)。
5. **風險結構良好,非賭**:C 的困難知識(schema/終止語意/resume 梯度/雙判據失效證據)全在 A 期付清,是「搬運不是探索」;SDK Agent 介面深度已由 spike 驗完(`spike_rawcli_agent.py` 4/4 PASS,`Conversation(agent=…)` 接受外部子類,確定不用 fork)。

B 並非全輸 —— **併發是它相對裸跑真正買到的東西**:`demo_concurrent.py` 實測 1 個 agent-server 進程(PID 7944)同時管 4 個 conversation,各自 workspace/事件流、grader 4/4 互不污染,總併發 wall-clock 37s ≈ 最慢單張(非 4× 串行)。任務越要規模化(v5 D10 max_running 8),agent-server 地基價值越浮現;單張 trivial 任務用 rawcli 裸跑更省。這正是「架構讓你不用賭」—— profile 一行在 rawcli 與 openhands-server 間切換。

## 對 ARCP 的影響

- **envelope 契約跨 backend 統一**:2026-08-06(W5.4)quota 解禁後補跑,**三 backend × 雙引擎 6 格全綠** —— rawcli / openhands-acp / openhands-server × claude / codex 共用同一 envelope 契約,dispatcher / grader / 三態 **零改動**。差異化層(grader / recovery loop / escalation / transcript)runtime-agnostic 至此全矩陣實證。
- **B→C 遷移面窄**:Jira outer loop(routing/watermark/指令通道/欄位所有權)100% 存活;workspace/profile YAML ~95%;grader/verify/三態 100%;要拋棄的只有 ACP adapter 那一層(本來就是要換的)。
- **rawcli 純 stdlib、免 venv**:W5.5 讓 rawcli **脫離 OpenHands 依賴** —— 純 stdlib,省 591MB venv。等於把 C.5 對照表裡 C 的「venv」欄位進一步收斂為零重依賴,兼得 A 的零依賴精神與 C 的骨架能力(需 GUI/併發時再掛 openhands-server backend)。
- **headless 是自動化地基,tmux 不取代它**:tmux 取代 headless = 失去結構化可觀測性(grader/狀態機/stall/細粒度/resume 全靠 stream-json)。tmux 唯一真優點是「人 attach 接管」,可作為可選 backend 並存,但「連進去 debug」已由 detail page 用結構化、唯讀安全、5s 實時刷新的方式滿足。

## 原始出處

- [A vs B 實跑對照 COMPARISON.md](../../examples/openhands-acp-poc/COMPARISON.md) —— 248 vs 14 事件、A/B/C 三方對照 §6、併發 §7、6 格矩陣補記
- [路線 B 落地計畫 PLAN.md](../../examples/openhands-acp-poc/PLAN.md) —— litellm rust-wheel / 90s startup / 批次無 kill 窗口 等陷阱實錄
- [A/B/C roadmap 策略分析](2026-08-abc-roadmap-analysis.md) —— 三案定義、GAP、B→C 存活率、可行性判定
- [OpenHands × ACP × Claude Code 生命週期研究](2026-08-openhands-acp-claude-code-lifetime.md) —— lazy spawn、ACP 握手、Evict/resume、bypassPermissions 行號級分析
