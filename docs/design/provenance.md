# DESIGN_provenance — 過程存證 + 結案回寫(Q 波,2026-08-13 定案)

> **一句話**:讓一張票**離開 ARCP 之後,在 Jira 上仍能自證全程**——TICKET.md
> 每個版本、帶時戳的事件軌跡、session 快照都在票上;結案時 description 置頂
> 一眼可見的結果區。實作 `src/arcp/provenance.py`;同波 P=TICKET.md 變數插值
> (`workspace.interpolate`)。

## 動機(使用者原始需求)

Jira 是紀錄機制(debuggability/traceability/L4 素材)。但 ARCP 的細節
(TICKET.md、journal、transcript)都在 server 的 runtime/——離開 ARCP(或
runtime 被 retention 回收)就看不到過程。把關鍵存證**上傳到票上**,Jira 自足。

## P — TICKET.md 變數與插值

- **三鍵維持**:`crid:` / `email:` / `prompt:`(description 頂部 yaml 區,
  正本 [lifecycle.md §4.2/§5](lifecycle.md))。
- **插值(新)**:`{crid}` `{email}` `{prompt}` `{key}` 占位符在**文本類**代入:
  - profile `goal`、TICKET.md 描述段(含安全審修訂版)、人類指示段
  - workspace 根的 `CLAUDE.md` / `AGENTS.md`(provision 一次性;代入後冪等)
  - **verify cmd 不代入**(值來自 description=任何人可寫,插進 shell=注入面;
    要用變數的驗收改用檔案驗證)
  - 未知占位符(如 `{foo}`)一律**保留原樣**,單 pass 不遞歸。
- 用途:同一個 profile 泛用於不同 CR——goal 寫「分析 {crid} 的 Coverity
  報告」,每張票渲染成實際 CR 號;skills 檔不插值(共用資產,教 agent 讀
  TICKET.md 取值)。

### 候選變數(1A 定案:**只列此表、看不出需求不實作**)

| 候選 | 用途 | 不做原因/條件 |
|---|---|---|
| `profile:` | 人工直接指定 profile(繞 route/triage) | 等真需求;現有 label+route 夠用 |
| `base:` | 開票即指定跨票交接來源 | 等自動化開票需要時 |
| `repo:` / `branch:` | CR→程式碼位置(內網 fix agent) | **內網落地時最可能先做**;workspace 佈建據此 checkout |
| `priority:` | 排隊插隊 | 等真的塞車 |
| `model:` / `budget_usd:` | 單票 override | 成本治理風險;若做只認 admin 開的票 |

## Q — 過程存證(2A)

`attach_ticket_md_if_changed`(dispatcher 每輪 provision/health 刷新後呼):
- TICKET.md **內容真變才傳**(sha256 前 16 碼比對,sidecar `.arcp_ticket_hash`)
- 附件名 `TICKET_<key>_<yyyymmdd-hhmmss>.md` → Jira 附件列表=版本歷史,
  回放「agent 當時看到什麼」(一般票 2–4 版:首建/安全審修訂/每次人類指示)
- 失敗不推進 hash(下輪補傳);journal `ticket_md_attached`

## Q — 結案回寫(2B/2C;close 與 cancel/abort 同規格)

`finalize_provenance`,五個收尾點全接:人評 close(hil)、auto_close
(scoring)、指令台 cancel(commands)、triage 判不出(dispatcher)、
安全審 abort 與 base 交接(hil 統一「終局判定」:提交後 outcome=ABORTED
或有 closed 事件)。

**(1) description 置頂結果區**——`[ARCP owner=result]` 段(排序在 human
之前=最上面;沿用 sections hash 機制,區塊外不碰):

```yaml
result: SUCCESS              # 或 ABORTED(reason=security/cancel/untriageable…)
score: 人評 8/10 · agent 自評 9/10
cost: $0.1234 · 45,231 tokens · 2 attempts
time: 執行 18m · 等人 2h05m   # journal 配對粗算(分鐘級)
crid: CR-123                  # 有才列
evidence: timeline_….jsonl · SESSION_….md · final.html
server: /…/runtime/workspaces/…   # ARCP 主機上的工作區路徑
dashboard: http://…:8788/ticket/<key>  # config source.dashboard_url 有配才列
closed_at: 2026-08-13T14:22
```

**(2) 存證附件**(>6MB 不附、結果區留 server 路徑;沿 deliverables 門檻):
- `timeline_<key>.jsonl` — journal 該票全事件切片(帶時戳;L4 未來讀這個)
- `SESSION_<key>.md` — session 全欄位快照(人可讀表格)
- `final.html` — transcript 定格(finalize 已產;沒有就算了)
- (選配未做)timeline.html 單檔視覺化——jsonl 已可回放,順手才做

**best-effort 不變量**:存證/回寫任何一步失敗只 log+journal,**不擋收尾**
(附件是證據,不是閘門)。journal:`provenance_attached`、`result_written`。

## 與其他文件

TICKET.md 組成正本 [workspace.md](workspace.md);變數契約 [lifecycle.md](lifecycle.md);
HIL 全表 [interaction.md §3.2](interaction.md);測試 `tests/test_provenance.py`。
