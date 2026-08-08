# DESIGN_transcript — transcript HTML 可視化(W4/V0 研究記錄)

> 2026-08-06 V0 除險結論。目標:claude / codex session(含 sub-agent)產
> human-readable HTML,支撐統一快照器(active 每 N 秒 + 離手 final + close 打包)。

## 工具選型(定案:vendor claude-code-log)

| 候選 | 結論 |
|---|---|
| **claude-code-log**(https://github.com/daaain/claude-code-log,MIT,Daniel Demmel;本機 clone `~/git/claude-code-log` v1.5.0) | ✅ **採用,vendor 進 `vendor/cclog/`**。Python(同棧可 refactor)、原生渲染 sidechain/agentId(sub-agent)、`--provider codex --session-id`(beta)、jinja2 模板可控 |
| cchv-server(claude-code-history-viewer prebuilt) | ✗ 棄用。實測 `--export` 可產 HTML(claude session-id、codex 絕對路徑都通),**但 source `export.rs:330` 證實 export 刻意丟 sidechain**("drop sidechains"),且 Rust binary 無法按需 refactor |

vendor 原則(使用者 2026-08-06 指示):必要模組 copy 到專門資料夾、適當 refactor、
**註明出處**(NOTICE 檔:上游 URL/版本/commit/MIT 全文)。剝離不需要的:TUI
(textual)、瀏覽器開啟、git 整合(gitpython)、cache/watch。保留:
parser/models/converter/renderer + html 模板 + providers(codex)。
依賴(pydantic/jinja2/mistune/dateparser/click/pygments)裝 `tools/cclog/.venv`
專用 venv,不污染 harness 其它環境。

## session 檔案格局(實測確認)

- **claude 主 session**:`~/.claude/projects/<cwd-slug>/<session-id>.jsonl`
  (rawcli 以 workspace 為 cwd → slug 含 instance 路徑,session_id 在 envelope)
- **claude sub-agent(新版)**:`~/.claude/projects/<cwd-slug>/<session-id>/`
  `subagents/agent-<id>.jsonl` —— **獨立檔,glob 即枚舉**;行內
  `isSidechain: true` + `agentId` + `parentUuid`
- **codex**:`~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<thread-id>.jsonl`
  (thread_id 在 envelope session_id)
- 我們另有 per-attempt 原生流備份:`<instance>/attempts/aN.raw.jsonl`
  (stream-json,格式 ≠ ~/.claude session 檔;僅作保真備份,不餵 renderer)

## wrapper 介面(W4.0 產出)

```
tools/cclog/render_transcript.py
  --claude-session <session-id 或 .jsonl 路徑>   → 主 session HTML
  --subagents                                    → 順帶產每個 agent-*.jsonl HTML
  --codex-session <thread-id 或 rollout .jsonl>  → codex HTML
  --output-dir <dir>
```
(被 W4.2 transcript.py 的 snapshot/finalize 呼叫;獨立可手跑除錯)

## 已驗事實(cchv 時期實測,對新方案仍有效)

- claude session id 可從 envelope 拿(dispatcher 已存 store)
- codex rollout 檔名含 thread id,find 一下就到
- SCRUM-22 session(16 msgs)、e2e codex(43 msgs)、含 59 行 sidechain 的
  agent-*.jsonl 都是現成測材
