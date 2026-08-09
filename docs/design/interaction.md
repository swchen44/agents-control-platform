# DESIGN_interaction — 互動服務(W11):HIL 人機介面

> 2026-08-08 討論定案(使用者主導 + 修正一份無背景 AI 草案)。取代「人直接編 Jira
> description free-text」的人機介面。與 [lifecycle.md](lifecycle.md)、
> [architecture.md](architecture.md) 的 HIL 模型銜接。
> **屬 runtime 行為,本文件先只寫設計,程式待實作(W11)。**

## 0. 動機 + Jira 的角色

**Jira 的角色(世界觀)**:Jira = 對外的**工作日誌 + 系統帳本(System of Record)**;
Agent 以**員工**身分接單 → 做事(後台)→ 更新進度 → 回報成果讓人評分關單。真正的工作與
細節在後台(workspace / dashboard / transcript);Jira 承載策展後的摘要/決策/結果/連結。
本互動服務就是讓「員工(Agent)與主管(人)」在這本工作日誌上互動的**受控介面**。

用 Jira description free-text / comment 下指令給 agent **易出錯、難處理**。改成:人類所有
輸入都經**受控表單**(一次性連結),系統再把結果回寫 description 對應區段 + comment 存證
——正是為了把 Jira 維持成一份乾淨可稽核的日誌,而非 free-text 聊天室。

## 1. 核心原則

- **assignee 恆定 = Agent**:自始至終不因需要人介入而變更。
- **單一寫入者**:Description 欄位與 workflow state **僅由 Agent/系統寫入/轉換**;人類不
  直接編 description、不直接轉 state。系統只認特定 section(做 hash 檢查);人在 section
  外亂寫沒關係,比對到 section 被竄改則產生**可見告警**。
- **結構化輸入**:人類輸入一律經受控表單產生,不接受 free-text comment 作為結構化資料源。
- **通知與所有權分離**:通知用 **@mention comment**,不用 assignee 變更、不用 workflow
  轉換作為通知手段。
- **可稽核**:每次人類輸入都能回答「誰 / 何時 / 送了什麼 / 是否事後遭竄改」。

## 2. 元件與安全模型

**獨立進程/port 的互動服務**(人面向,內網桌機+行動,token 授權)。三種安全模型分離:

| 服務 | 對象 | 讀寫 | 授權 |
|---|---|---|---|
| detail_server(dashboard) | 人(唯讀觀測) | 唯讀 | 內網 zero-auth |
| control_api | 管理者/自動化(內部控制) | 寫(控制) | 127.0.0.1 |
| **互動服務(本設計)** | **人(填表)** | **寫(回 Jira)** | **一次性 token(capability URL)** |

回寫 Jira 由 poller 每輪執行(見 §7 異常處理:**不做 work queue**)。

## 3. HIL 互動流程

1. **Agent 需要人** → 在票新增 comment,內含 `@mention` 指定對象,附:請求目的簡述、
   **一次性連結**、有效期、**Request ID**。**不以變更 assignee 通知**。
2. **人開連結** → 受控表單:顯示 ticket 上下文(key/標題/agent 目前狀態/本次問題),
   使人不需另開 Jira 即可作答;表單由**版本化 Form Schema** 產生;送出前**前後端各驗一次**
   (必填/型別/值域)。
3. **人送出** → 系統寫回 Description 對應 **Human Section(加 hash + 日期)**,並新增一則
   **稽核 comment**(回填摘要/提交者/提交時間/Request ID)作時間序紀錄。
4. **表單提交 = HIL resume 觸發**(取代 W10 的 assignee→機器人)。

### 3.1 表單型別(至少,可擴充)

| 型別 | 對應 HIL | 用途 |
|---|---|---|
| `need_info` | HIL(Middle) | Agent 缺資訊,請人補充 |
| `decision` | HIL(Middle) | Agent 提選項,請人擇一 / 核可(含 triage 選 profile、審批) |
| `score_and_close` | HIL(End) | 生命週期終點:評分 + 結案裁決 |

`score_and_close` 呈現三訊號供對照:**grader**(SUCCESS/FAILURE/UNKNOWN,證據型)、
**agent 自評 0–10**、**人類 0–10**;並含**關票裁決**(見 §8)。

## 4. Token 規格

- 每個 Interaction Request 產生唯一且**不可預測** token(≥128-bit 亂數)。
- **有效期綁請求生命週期**(票進終態即全失效;另設可設定的短窗),逾期開啟顯示「已逾期」+
  聯絡指引,**不顯示表單**。(修正 AI 原案的固定 3 個月:一次性互動不該讓 token 活 3 個月。)
- 成功提交後**立即失效**;重複開啟顯示「已提交」+ 唯讀摘要。
- 綁**單一 ticket + 單一 Request ID + 單一表單定義**,不可跨票重用。
- 與常駐 Detail Page(Agent Link 欄,§9)為**不同物件**,不共用 URL。
- token 不得出現在共用日誌 / 外部監控等非預期位置。

## 5. Description 回寫 + 稽核

- 提交結果寫入該票 Description 的對應 **Human Section**,以機器可解析標記包夾(沿用
  `sections.py` 的 `[ARCP owner=human]` 區段),使系統能重複定位/更新/驗證。
- Human Section 改由**系統(從表單)寫**,故可**加 hash + 日期**;人若手改 → hash 不符 →
  `verify_and_restore` 告警(sections.py 既有能力)。
- 同步新增稽核 comment(摘要/提交者/時間/Request ID)。
- **冪等**:回寫具冪等性,重複執行不得產生重複 Human Section 或重複 comment。

## 6. assignee 處理

- 恆定 = Agent。若被改離 Agent → **記 journal 告警 + 貼一次 comment 提醒**(冪等,不每輪
  洗版),**不強制改回**(避免搶 assignee + revert 觸發的通知噪音)。

## 7. Jira 異常處理(簡易版,**不做 work queue**)

使用者定調:GRA(Jira)有時會中斷;異常處理要**完整但不複雜**,且**不做佇列**(怕不同步)。

- **健康偵測**:寫入失敗 / health probe 失敗 → 系統進「降級暫停」(停寫入 / 停派工),
  **不佇列**。
- **人開表單時先測 Jira 健康**:異常 → 表單頁**明示「目前異常,請先檢視、暫勿送出」**;人
  仍可先看、先填;若仍送出 → 系統**直接回報異常、不落地**(不排隊、不假裝成功)。
- **恢復**:自動(probe 回正常)或**管理者手動**(dashboard / 管理頁按「已恢復」通知
  poller)→ 系統續跑、維持正常。不論自動或手動恢復都要讓系統回到正常運作。

## 8. score_and_close 的關票裁決

- 送出 `score_and_close` → **系統幫忙把 Jira 轉 Done**(option a)。
- 即 **state 由系統/Agent 寫入**(呼應「單一寫入者」),人透過表單**授權**關票。
- 細化 W10 的「harness 不主動 transition、關票=人做」→ **「人授權、系統執行轉 Done」**。

## 9. 催辦 / 異常記號(v1)

- 回應期限(預設 **1 天**,可設)→ 逾期**重 @mention** 催辦。
- 多次(預設 **10 次**,可設)仍無回應 → 在 DB 記**異常計數** + 留 comment,供統計。

## 10. Agent Link 欄(v1)

把**常駐 Detail Page**(dashboard `/ticket/<id>`)連結寫進票的一個欄位,讓人隨時點進觀測。
與一次性連結(§4)**不同物件**。

## 11. REST API(v1)

互動服務能力開成乾淨 REST,供**未來人類自己的 agent**(Hermes / openclaw 類)代理作答/
先檢查。**proxy 本體遠期**,v1 先把 API 形狀定好。

## 12. 與 W10 的關係 / 對既有設計的改動

- 這是 HIL 的**具體人機介面**,取代「人直接編 description human 段 / assignee→bot 觸發」。
- `need_info`/`decision` = HIL(Middle)(triage/審批/補資訊);`score_and_close` = HIL(End)。
- **要改**:`dispatcher.py` 不再主動 `assign` 人;`external.py` 不再以 assignee 為訊號;
  `DESIGN_lifecycle` 的「assignee=資源開關」改寫;`sections.py` human 段改系統寫+hash;
  poller 加 Jira 健康偵測 + 降級暫停/恢復 + 回寫執行。
- **屬 runtime 行為 → W11,先不動碼**(與 W10.2 HIL 行為、W10.3 a2a 一併待實作)。

## 13. 人機互動增修(2026-08-09 group A 定案,待實作)

逐題決策樹(Q9–Q13)敲定,設計如下;實作為新功能,尚未動碼。

### 13.1 control path / data path 模型(Q9/Q12)
- **control path = 餵 CLI 的 prompt**(祈使、瞬時、harness 主動控制,含「TICKET.md 已更新,
  請重讀」);**data path = TICKET.md**(落地、每輪刷新、agent 被指向後重讀);
  **行為守則 = CLAUDE.md/AGENTS.md**(session 啟動自動載入)。
- 事實:`claude -p`/`codex exec` **不會**自動監看檔案變更重讀;TICKET.md 不是特殊檔,
  agent 讀它是因為 prompt 指向。→ 不能只靠 CLAUDE.md/AGENTS.md。
- 「harness 主動在 resume prompt 提示 TICKET.md 更新、使用者無感」**不違反原則**:harness
  本就是 prompt 單一控制者。守則:主動 prompt 不得造成重工(native resume 保證)、不得繞過 grader。

### 13.2 agent 數字自評 0–10(Q13)
- **只在關單時做一次**(非每 attempt):經過 HIL/handoff、不一定一次完成,只有人類要 close +
  給分那刻才有意義。ScoreGate 產 `score_and_close` 表單前,**resume + prompt 問 agent 自評**,
  連同 grader 判定、人類欄一起顯示三訊號。成本 = 每票關單一次 agent 呼叫。

### 13.3 HIL 表單自由 prompt 欄(Q10)
- HIL 表單加一個選填「給 agent 的補充指示」自由欄;submit 後**累加寫進 TICKET.md 的
  「人類指示」段**(帶時間),resume prompt 指向重讀。
- 儲存:TICKET.md 每輪由 code 重渲染,故人類指示存**sidecar `ws/.arcp_human.md`**(append-only)
  ,`render_ticket_md` 讀它成「## 人類指示(累加)」段 → 不被重渲染蓋掉、可稽核、單一寫入者。

### 13.4 人類強制中斷 → 回 HIL(Q11,`@agent hold`)
- 新 `@agent hold` 指令(comment 通道)→ **立即 evict(沿用現有 killpg)** → HIL(Middle) →
  開 need_info 表單(含 13.3 prompt 欄)→ submit 寫 TICKET.md 人類指示段 + resume 排隊。
  **不耗 attempt**。九成沿用現有(evict + HIL 表單 + 指令通道),新增 = 指令 + 串接。
- ⚠️ **限制(寫進開發者手冊 FAQ)**:立即 killpg = 進行中的工具步驟被硬殺;不丟資料
  (native resume 下輪重跑那一步),但那一步會重跑。未做「SIGTERM→10s→SIGKILL」優雅停,因
  native resume 已保進度、grace 效益低。此為已知現象,debug 時據此理解。
