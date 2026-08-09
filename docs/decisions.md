# 決策記錄(ADR-lite)

關鍵取捨與「為什麼」。逐項需求的 Why 見 [requirements.md](requirements.md);此處只收
**跨系統、影響深遠**的決策。新決策往上加。

---

### D1. Jira = 工作日誌 / System of Record;Agent = 員工
Jira 是對外的工作日誌;Agent 以員工身分接單→做事→回報→被評分關單。真工作與細節在後台
(workspace / dashboard / transcript),Jira 只承載策展後的摘要/決策/結果/連結。
**Why**:公司本來就活在 Jira,讓 agent 對 Jira 負責 → 人用既有管理儀式管一支 agent 大軍。
這也是 assignee 恆定、受控表單、單一寫入者、hash 稽核的共同動機。

### D2. 證據型停止(grader 終審),非 agent 自稱
agent 說「完成」不算數;profile 的確定性 `verify` 過才 SUCCESS。「loop on evidence, not
confidence」。**Why**:避免假完成。

### D3. 三態 outcome(SUCCESS / FAILURE / UNKNOWN)
**Why**:分不清「失敗」與「無法證明」會誤重試燒錢或漏處理;UNKNOWN 只有人能解。

### D4. envelope 契約跨 backend 不變
三 backend × 雙引擎共用 `{completed, session_id, cost, error, …}`。
**Why**:差異化層 runtime-agnostic,換執行單元 dispatcher/grader 零改動。

### D5. 內網零外部依賴
dashboard 不吃任何外部 CDN/字型/元件;相依一律 vendor 進 repo(vis-timeline /
swagger-ui / svg-pan-zoom / claude-code-log)。**Why**:內網環境可用、可離線、可稽核。

### D6. 生命週期用 HIL 模型(6 態)
`todo / running / queued / HIL(Middle) / HIL(End) / aborted`,`closed` 為概念終點。
success/failure/unknown 是 **HIL(End) 的結果屬性**,不是頂層狀態;舊「交人 inactive」+
「等待人類 pending」合併成 **HIL(Middle)**。**Why**:交人與等待人類語意一致(都=等人);
把「結果」與「誰持有票」兩維度分開,概念更乾淨。詳 [設計/生命週期](design/lifecycle.md)。

### D7. HIL(End) 三訊號:grader / agent 自評 / 人類評分
終態同時呈現 grader 判定(S/F/U,決定狀態)、agent 自評(0–10)、人類評分(0–10)供對照。

### D8. 人機介面 = 一次性 token 表單(取代人直接編 Jira description)
assignee **恆定=Agent**;通知用 @mention comment;人類輸入一律走受控表單(一次性連結);
系統把結果回寫 description 的 human 段(hash+日期)+ 稽核 comment;表單提交 = HIL resume
觸發。**Why**:用 free-text description/comment 下指令易錯、難處理;結構化表單 + 單一寫入者
+ 可稽核才穩。詳 [設計/互動服務](design/interaction.md)。

### D9. Jira 異常用「暫停/恢復」,不做 work queue
Jira 寫入/健康失敗 → poller 降級暫停(停寫/停派),poll 成功自動解除或管理者 `POST
/recover`;人開表單時異常則「暫勿送出、不落地」。**Why**:work queue 有不同步風險;
circuit-breaker 式暫停/恢復更簡單可靠。

### D10. agent↔agent 交接:同票換手(next) vs 跨票換手(base)(對等,依場景選)
- **同票換手(next)**:同一張 Jira 票,重置 session(session_id/attempts 歸零)、pin 新
  profile、重新 provision workspace(**非** native resume)。觸發:HIL 表單「同票換手」/
  `@agent next`/agent 自發。適合「同一件事換人/換引擎重新來過」。
- **跨票換手(base)**:系統 `create_ticket` 另開新票、預建 pinned session(`base_ref`)、
  本票收成 **ABORTED**;新票首次佈建後注入 `BASE_<key>/` 脈絡(來源票 TICKET.md/末次
  envelope + 指路)。觸發:HIL 表單「跨票換手」。適合「換引擎/重開/跨專案/人策展重啟」。
**Why**:延續同一件事 vs 另開新票承接敘事脈絡,兩場景各有適用。兩者**都是乾淨重啟**
(不吃舊 workspace 的 native resume),差別在同票 vs 新票、以及是否注入來源脈絡。

### D11. 併發用 F1 分層額度閘(global + per-engine + per-profile)
超額 QUEUED(FIFO);HIL/終態不占額度。**Why**:怕機器 CPU/memory 不夠用。

### D12. 冪等:agent 層 native resume + harness 層「先持久化再外寫」
**Why**:harness 中途 crash 不重花錢(at-most-once);重 poll 不重放歷史。

### D13. 專業化:src-layout + uv + MIT + GitHub CI/CD
`src/arcp/` 套件、pyproject(hatchling)、uv.lock、Python 3.10–3.13 矩陣 CI(ruff+build+
離線測試)、tag→GitHub Release CD。**Why**:讓別人能安裝/貢獻;CI 在 fresh checkout 抓
本機測不出的問題(如依賴 gitignored venv)。

### D14. 省電優先:不用 caffeinate
**Why**:筆電沒充電耗電太快;長跑靠 run_poller 迭代 timebox,不防睡(睡醒能續)。
