# B 期 Harness — Jira Outer Loop 開發計畫與 Checklist

> 依 v5 設計(research/2026-08-jira-agent-harness-design-v5.md)P0/P1 範圍 +
> A/B/C 策略(research/2026-08-abc-roadmap-analysis.md)B 期三守則。
> **作業方式:單線、小步、每個 Phase 完成即 commit+push。**
> **斷網/斷線 resume 指引:讀本檔 checklist + `git log --oneline -10`,
> 從第一個未勾項目繼續;所有執行狀態都在磁碟(SQLite/journal),無記憶體依賴。**
> 最後更新:2026-08-03。

## 環境(已就緒)

- Jira:Atlassian Cloud `swchen44.atlassian.net`,project **AGT**(agent 專屬,v5 D6)
- 憑證:`~/.env`(JIRA_BASE_URL / JIRA_EMAIL / JIRA_API_TOKEN)——**絕不入 repo**
- 正式環境是公司 Jira Server → Cloud/Server 差異全部關進 source-adapter 層(v5 D6b)

## 設計約束(來自 v5 與 B 期三守則)

1. outer loop YAML 只決定「歸哪個 profile / 何時接管 / 何時交還」,禁止步驟序列(C1)
2. 主鍵 = Jira 數字 issue_id,不是 ticket key(C3)
3. comment watermark + issue_id+comment_id 冪等(§6-8/9)
4. regex 載入時預編譯,設定錯誤啟動即炸(§6-7)
5. 消費端只依賴正規化 Ticket 模型與我們的 event/journal——B→C 換執行單元不動 outer loop
6. outcome 三態 SUCCESS/FAILURE/**UNKNOWN**;UNKNOWN 只能人解除(v5 D3)
7. source 層 stdlib-only(零依賴,與 A 路 PoC 同哲學);inner loop 才用 openhands venv

## Checklist

**Phase 0 — source adapter + 認證冒煙** ✅ 2026-08-03
- [x] `arcp_harness/config.py`:讀 `~/.env`(不落地、不印值、不進 os.environ)
- [x] `arcp_harness/ticket.py`:正規化 Ticket 模型(數字 id 主鍵,v5 C3)
- [x] `arcp_harness/jira_source.py`:Jira Cloud REST v3 adapter(urllib+basic auth;
      /search/jql 新端點+舊端點 fallback;ADF 攤平/組裝;certifi SSL——
      陷阱實錄:python.org macOS build 不帶系統 CA)
- [x] `smoke_jira.py`:auth PASS(myself)+ AGT search PASS
- [x] commit+push

**Phase 1 — routing + watch + watermark(notify_only 灰度)** ✅ 2026-08-03
- [x] `routes.yaml` + `arcp_harness/routing.py`:regex 預編譯載入即炸、
      steps:/then: 直接拒絕(C1 護欄)、when AND/陣列 OR、首個 match 勝出
- [x] `arcp_harness/store.py`:SQLite(WAL + BEGIN IMMEDIATE)、issue_id 主鍵、
      comment watermark、events.jsonl journal
- [x] `arcp_harness/poller.py`:poll → diff → new_issue/comment_added/
      status_changed/assignee_changed → routing → route_matched(只記不動)
- [x] E2E 灰度 4/4 PASS(真實 Jira,SCRUM-1):route_matched 命中、不接管、
      重複輪詢零重放、comment watermark 生效
- [x] commit+push

**⚠️ 環境事實(2026-08-03 實測釘住)**:帳號下 project 名稱是 AgentLifetimeBoardv1
但 **key = SCRUM**(Jira 改名不改 key;「AGT」不存在——新 /search/jql 端點對
不存在的 project 回空集合不報錯,別被騙)。issue type 是中文(任務=10003)。
未來若開真正的 AGT project,改 routes.yaml 兩處即可。

**Phase 2 — dispatch 到 inner loop(B route 執行)**
- [ ] workspace provisioning:`tickets/{issue_id}/` + skills 注入(.claude/skills)
      + TICKET.md 渲染(ticket 欄位)
- [ ] inner runner:openhands venv 的 ACPAgent 跑單次任務(subprocess 呼叫
      openhands-acp-poc 環境;agent 設定獨立區塊——B→C 只換這塊)
- [ ] grader/verify(沿用 arcp_poc.grader)+ **三態 outcome**(UNKNOWN:子行程
      消失/無法證明副作用 → pending:unknown)
- [ ] mapping 表 create_or_resume(acp_resume_session_id 續用)+ workspace health check
- [ ] 結果回寫 AGT:Resolve comment 帶證據(verify 結果/變更檔案/attempt/成本)
- [ ] E2E:AGT 真票 → 接管 → 執行 → 驗證 → 回寫
- [ ] commit+push

**Phase 3 — 指令通道 + 外部變更防護**
- [ ] `@agent` 指令(run/stop/retry/cancel)+ commenter 白名單 + ack 回覆 + 冪等
- [ ] external_change_policy:Cancelled → 立即中斷;assignee 改走 → pending
- [ ] pending 三分類(human-decision/external/unknown)
- [ ] commit+push

**每 Phase 完成判準**:E2E 可重現 + selftest 級免費驗證 + 文件同步。

## 里程碑

M1(Phase 0-1)= 灰度上線:真票被正確路由但不接管——零風險。
M2(Phase 2)= 第一張票端到端:AGT 開票 → agent 做完 → 帶證據回寫。
M3(Phase 3)= 人機協作閉環。
