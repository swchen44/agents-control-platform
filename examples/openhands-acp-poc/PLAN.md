# OpenHands ACP PoC — 開發計畫與 Checklist(路線 B 落地)

> 目標:用 **OpenHands SDK** 以 headless 方式跑 **claude 與 codex**(經 ACP adapter),
> 與路線 A(`examples/jira-agent-poc/` raw supervisor,**留存不動**)做實跑對照。
> 每個 Phase 完成即 commit + push main。最後更新:2026-08-02。

## 0. 範圍與不做的事

- ✅ SDK **in-process**(`openhands.sdk` 的 `ACPAgent` + `Conversation`)——最貼近 headless,
  不起 agent-server、不起 Docker(agent-server 列為 stretch,見 Phase 5)。
- ✅ 對照任務與 A 相同(循序建檔 + 確定性判準),結果才可比。
- ❌ 不改 A 的任何碼;B 需要 grader 時直接 import A 的 `arcp_poc`(同 repo,不複製)。

## 1. 已釘死的環境事實(2026-08-02 實查)

- SDK:`~/git/openhands/software-agent-sdk/openhands-sdk`,v1.39.1,`requires-python >=3.12`
  (本機 3.13 ✓);依賴重(litellm/fastmcp/lmnr/tree-sitter…)→ **venv 隔離,不進 git**。
- ACP adapter(SDK `acp_providers.py` 內建 pin):
  claude → `npx -y @agentclientprotocol/claude-agent-acp@<pin>`;
  codex → `npx -y @agentclientprotocol/codex-acp@<pin>`。node v26 / npx 11 ✓。
- SDK 的 ACPAgent 已含:`session/load` resume、denial/approval 事件、
  bootstrap transcript 渲染器(未接線)——見 research v3 §6.4。

## 2. 架構決策(提案,可否決)

| # | 決策 | 理由 |
|---|---|---|
| B1 | SDK in-process 為主線,agent-server 為 stretch | headless 最少件;server 留對照 |
| B2 | 新資料夾 `examples/openhands-acp-poc/`,venv 在 `.venv/`(gitignore) | 原 PoC 留存 |
| B3 | 對照任務 = A 的循序建檔 + 同一 grader 判準 | 可比性 |
| B4 | 事件流照 A 慣例落地(journal + fixtures) | 協定回歸測試 |

## 3. 開發 Checklist

**Phase 0 — 環境**
- [ ] `.venv` 建立、`pip install -e` 本機 SDK clone
- [ ] `python -c "from openhands.sdk import ..."` import 冒煙
- [ ] `.gitignore` 排除 `.venv/` 與 runtime 輸出

**Phase 1 — claude via ACP,headless 冒煙**
- [ ] ACPAgent(claude-code)+ Conversation 跑 trivial 任務到完成
- [ ] 事件流(OpenHands 事件)落地 journal
- [ ] 完成判定與 session id 擷取方式記錄

**Phase 2 — codex via ACP,headless 冒煙**
- [ ] 同 Phase 1(codex-acp adapter 相容性是本 phase 的實驗目的)

**Phase 3 — A/B 對照實驗**
- [ ] 循序建檔任務在 B 路線跑,A 的 `FileChecklistGrader` 驗證
- [ ] 事件粒度對照:B 的事件流 vs A 的原生 stream(逐事件對表)
- [ ] 產出 `COMPARISON.md`(trace 粒度/控制面/setup 成本/依賴面,全部實跑撐)

**Phase 4 — 文件與回寫**
- [ ] research v3 §6.4 / §9.3-4 由「分析推論」升級為「實跑對照」
- [ ] HANDOFF / 兩層 README 更新

**Phase 5 — Stretch(另行決策後才做)**
- [ ] crash→resume via `acp_resume_session_id`(對照 A 的 recovery 矩陣)
- [ ] agent-server 模式(REST/WS)對照
- [ ] denial→approval 事件對照(vs A 的 escalation)

## 4. 自我驗證判準

- V1 claude via ACP:trivial 任務 headless 到完成,事件落地。
- V2 codex via ACP:同 V1(若 adapter 不相容,如實記錄失敗形態即為結論)。
- V3 對照表每格都有實跑證據(沿用 v3 §4.1 的證據級別標註慣例)。
- V4 免 token 重驗:B 的捕獲事件流可離線 replay 檢查(照 A 的 fixtures 慣例)。

## 5. 風險與備援

- adapter 首跑走 npx 下載;版本已由 SDK pin,離線時會失敗 → 記錄即可。
- auth:adapter 是否吃本機既有 claude / codex 登入 **未知,Phase 1/2 的實驗目的之一**。
- SDK 依賴安裝若失敗:記錄失敗形態,fallback 改起 agent-server(Docker)另議。
- codex-acp 相容性未知(v3 只驗過 claude 側證據)——失敗本身就是有價值的結論。
