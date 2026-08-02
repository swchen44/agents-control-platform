# HANDOFF — headless agent 自動化 / ARCP

> 給「在此目錄開的新 session」的接手文件。前一段對話的 session 綁在
> `/Users/swchen.tw/git/openhands`(改名為「headless agent 自動化」)。本檔讓
> 零上下文的新 session 也能無縫接上。最後更新:2026-08-01。

## 0. 一句話目標

讓 `claude -p`、`codex exec` 這類 **headless coding agent** 能長時間可靠執行、
**可觀測(trace)**、**可控制(control)**,並能由 **Jira 事件驅動**:watch Jira
Server → issue 符合 assignee/keyword → 建工作資料夾 → 依 JSON 規則裝 skills →
headless 啟動 agent → 監控狀態。專案暫名 **ARCP**(Agent Runtime / Control Plane)。
**OpenHands 只是候選方法之一,不是前提。**

## 1. 現有交付物(都在本 repo)

- **研究報告 v3**:`research/2026-08-agent-runtime-control-plane-research-v3.md`
  (455 行;需求規格 + 從零寫開工級設計 + 三路線優缺點/維護成本 + 實測)
- **前版 v2**:`research/2026-07-agent-runtime-control-plane-research.md`
  (市場缺口 106-agent 對抗式驗證;v3 引用不重驗)
- **可跑 PoC**:`examples/jira-agent-poc/`(~770 行、零依賴、已實測跑通)
- **Crash-recovery 矩陣 harness**:`examples/jira-agent-poc/recovery_test.py`
  (2026-08-01 實測 4/4 PASS,詳 §4 與 §6-1)

## 2. 已敲定的決策(前一段對話,勿再問)

| # | 決策 |
|---|---|
| D1 | 報告放 `research/` 出 v3;舊的 openhands 目錄評估已併入 v3 §6 並刪除 |
| D2 | example 要「設計級片段 + 可跑 PoC」兩者 |
| D3 | 「從零寫」寫到**開工級**(需求 + 架構 + 介面定義) |
| D4 | 需求範圍**整合全部**(v2 七大能力 + Jira pipeline + trace&control) |
| D5 | 實作選項:**兩條主線深入**(raw / OpenHands ACP)+ 第三方簡述 |
| D6 | PoC 放 `agents-control-platform/examples/` |
| D7 | PoC 優先 **`claude -p` 和 `codex exec`**(使用者主力) |
| D8 | **raw 路徑設一級公民**,OpenHands ACP 留彈性作對照;三路線優缺點/維護成本要講清楚 |

## 3. 三條路線的實作狀態(重要:別誤以為三種都跑過)

| 路線 | research 有寫 | PoC 能跑 / 跑過 |
|---|---|---|
| **A. Raw**(自寫 supervisor 包 claude -p / codex exec) | ✅ | ✅ **真的跑過**:claude+codex live + replay + 7/7 self-test + claude/codex crash→resume 矩陣 |
| **B. OpenHands ACP**(agent-server 當底層) | ✅ | ❌ 只有註解 stub(`OpenHandsACPNote`),沒接沒跑 |
| **C. 從零寫完整 runtime** | ✅ | ⚠️ PoC 本體就是 C 的 MVP(= A);recovery/REST/dashboard 未做 |

**關鍵澄清**:A 與 C 在 PoC 裡是同一份程式碼(C 只是 A 補上 recovery/REST/dashboard)。
能實跑對照的其實是兩條:自寫 supervisor(已跑)vs OpenHands ACP(未跑)。
report §4 的三方優缺點表,**只有 A 欄是實測撐,B/C 是原始碼+文件分析推論**
(§4.1 已加證據級別標註)。使用者選擇維持分析對照,**未接 OpenHands ACP 實跑**。

## 4. 用實測釘死的事實(本機 2026-08-01,非文件推論)

- CLI 版本:claude **2.1.206** / codex-cli **0.142.5** / opencode(有 `opencode acp`)。
- **終止語意不對稱**:`claude -p` 有明確 `result` 事件;`codex exec --json` 沒有,
  靠 `turn.completed` + process exit → 統一層用「事件 OR exit code」雙判據。
- `claude -p --session-id <uuid>` 可**預先指定 session id**(crash recovery 重接的關鍵)。
- `codex exec` 非 tty 會讀 stdin → supervisor 必須 `stdin=DEVNULL`。
- 真實事件流存於 `examples/jira-agent-poc/fixtures/*.jsonl`(協定回歸測試用)。
- **claude crash→resume 已實測可靠(2026-08-01,2×2 矩陣 4/4 PASS、16/16 判準)**:
  「尚無產出」/「工具執行中」× SIGTERM/SIGKILL 殺掉後,`claude -p "繼續..." --resume <預指定 id>`
  皆重接成功——事件流帶**同一** session id、記得進度、**不重工**(crash 前檔案 mtime 不變)、
  任務補完;SIGKILL 斷在 thinking 中 session 檔也不壞。單 case $0.03-0.07(haiku)。
  crash+resume 真實流:`fixtures/claude_p_{crash,resume}_real.jsonl`(replay 回歸驗證過)。
- **codex crash→resume 亦實測可行(2026-08-02)**:thread id 從 `thread.started` **事後擷取**
  來得及 → `codex exec resume <id>` 重接成功(early×2 + midtool×SIGKILL 過;midtool×SIGTERM
  被實驗機睡眠污染,無乾淨數據點)。fixtures:`codex_exec_{crash,resume}_real.jsonl`。
- **本輪新釘死的陷阱**:① codex 收 SIGTERM 優雅退場 **rc=0** →「事件 OR exit code」雙判據
  會把中斷 run 誤判 DONE,完成與否只能靠證據型 grader(§6-2 因此升級為必要);
  ② `codex exec resume` **不吃 `--sandbox`**(rc=2),要 `-c sandbox_mode="..."`(driver 已修);
  ③ kill 必須 **killpg**,否則 codex 的 zsh 子程序孤兒續跑偷偷完工;
  ④ codex 工具粒度/服從度變異大(單指令打包/逐步/無視 sleep 都出現過),事件流不可當進度真值;
  ⑤ codex 會載入使用者 plugin(superpowers)增加變異,對照實驗宜 `--ignore-user-config`(未實測);
  ⑥ **筆電系統睡眠會凍結 supervisor 計時器**產生假 stall/假 hang——live 實驗要防睡
  (caffeinate 只擋 idle sleep,蓋螢幕電池睡眠擋不住),長跑監督應跑在不睡的機器。
- 三路線的 API 呼叫方式:raw = spawn CLI 吃 stream-json;OpenHands = `POST /api/conversations`
  (`agent_kind:acp`, `acp_server:claude-code|codex` 或 `acp_command:["opencode","acp"]`)
  + 訂閱 WS `/sockets/events/{id}`。

## 5. PoC 怎麼跑

```bash
cd examples/jira-agent-poc
python3 replay_demo.py     # 離線、免 token:真實事件流過 normalize→狀態機→trace
python3 selftest.py        # 免 token:7 項(rule 引擎 + 事件正規化 + 狀態機)
python3 run_demo.py claude  # live、花 token:真實 claude -p 全流程
python3 run_demo.py codex "... bug ..."  # live:rule 命中→裝 skill→codex exec
python3 recovery_test.py   # live、~$0.2:claude crash→resume 2×2 矩陣 + 確定性判分
python3 recovery_test.py --agent codex   # 同矩陣走 codex(建議 caffeinate 防睡眠)
```
PoC 模組:`arcp_poc/{events,drivers,supervisor,rules,workspace,jira_watcher}.py`;
規則 `rules.json`;範例 skill `skills/jira-bugfix/SKILL.md`。詳見該目錄 `README.md`。

## 6. 下一步(report §9.3 的 PoC 實驗清單,尚未做)

1. **Crash recovery 實測**:✅ **claude 基線完成(2026-08-01,4/4)**、
   ✅ **codex 基線完成(2026-08-02,3/4 時機,含最嚴苛 midtool×SIGKILL)**——皆 `recovery_test.py`。
   **剩**:codex midtool×SIGTERM 乾淨數據點(遇機器睡眠污染)、worktree 情境(issue #48835)、
   長跑/大 context resume、supervisor 內建「FAILED→自動 resume」迴路。
2. **證據型停止**:✅ **已實作(2026-08-02)**——`arcp_poc/grader.py` + supervisor 整合,
   DONE 需過證據、不過覆寫 FAILED(證據高於自稱);selftest 14/14。
   (背景:實測證實 codex SIGTERM 退場 rc=0,exit code 不能證明任務完成,§4。)
3. **Claude permission 行為矩陣**(v2 §2.3 第 5 點文件描述被 0-3 推翻,需實測)。
4. **OpenHands ACP 對照**:實作 `OpenHandsACPDriver` + 起 agent-server,同一 Jira 任務
   在 A/B 各跑一次,比 trace 粒度/控制/recovery/setup 成本(使用者目前選擇先不做)。
5. **opencode via ACP**:`acp_command:["opencode","acp"]` 實測相容性。
6. **waiting-permission → 開 Jira ticket** 升級迴路端到端。
7. **journal → transcript 降級 resume**(v3 §6.4):`--resume` 失敗時從 events.jsonl
   渲染 bootstrap transcript 注入新 session(抄 OpenHands `resume_transcript.py` 設計),
   形成「原生 resume → transcript 注入 → 全新重跑」三段梯度。

## 6.5 使用者立場備忘(2026-08-02)

- ACP/OpenHands resume 語意已原始碼查證(v3 §6.4):ACP 有 `session/load` 但底層
  耐久性與 raw 同源;OpenHands 有「存對話注入新 session」的 bootstrap-prompt resume
  設計(未接線)。
- **未來不排除直接拿 OpenHands 修改作基底**(他們把坑都走過一遍)——**持續觀察,
  暫不決策**;Driver 介面保持可插拔以保留此選項。

## 7. 建議路線(report 定案)

**主線 A(raw 一級)+ 可選後端 B(OpenHands 對照)+ C 只做差異化層。**
先用 A 把 Jira pipeline 跑起來(數天可上線內部用),Driver 介面留可插拔;
需要 crash-safe/程式化核准/Docker 隔離時再加 OpenHands ACP driver 對照;
若要做開源專案,只做 v2 驗證過「沒人做」的 **git checkpoint 語意層** 與
**跨 CLI 卡住偵測/recovery**,疊在 A/B 之上,不重造 runtime。

## 8. 與使用者協作的規則(務必遵守)

存在 memory `questioning-protocol`,重點:
1. 決策樹建模,系統性走訪每個分支/edge case。
2. **一次只問一個問題**,每問一題就暫停等回應。
3. 事實靠查閱(能從程式碼/環境讀到的絕不問),**只有決策才發問**。
4. 每個問題**都給選項**、附 AI 建議答案與理由(讓使用者審提案)。
5. **確認所有分支後才動工**,不提前寫碼/改檔。

## 9. 關鍵參考材料

- ChatGPT「Claude headless 解決方案」RFC 討論(headless 七大痛點、四層架構、
  Jira/Jenkins connector、RFC-0001~0010、Responsibility Matrix)。
- 三層排障地圖:`~/git/knowledge_from_ai_summary/personal-kb-repo/AI/
  2026-07-19-AGENT-HARNESS-VS-LOOP-VS-GRAPH-ENGINEERING-THREE-LAYERS.md`
  (Harness 管環境 / Loop 管回饋 / Graph 管流程;"Do not loop on confidence, loop on evidence")。
- OpenHands 本機 clone:`~/git/openhands/{OpenHands,software-agent-sdk}`(路線 B 依據)。
