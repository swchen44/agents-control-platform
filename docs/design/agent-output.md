# DESIGN — Agent 產出契約 + 人機介面(交付物 / Jira comment / HIL 表單頁)

> 2026-08-09 逐題(Q1–Q6)決策樹定案。解決:agent 完成時**把產出結構化回傳**,讓
> harness 貼回 Jira、並在 HIL 評分頁呈現,人才判斷得了「做完沒、做得好不好」。
> 這是 G1 結構化契約([contract.py](../../src/arcp/contract.py))的延伸 + 人機介面
> ([interaction.md](interaction.md))的強化。

## 洞察四欄(2026-08-16;學 trajectories summary 指導)

`OUTPUT.json` 選填欄:`decisions[]`(question/chosen/reasoning/impact)、
`conventions[]`(pattern/rationale/scope)、`lessons[]`(lesson/context/
recommendation)、`open_questions[]`——**空=合法、守則禁湊數**。渲染:
評分表單頁四小節(有才顯示)+Jira comment 一行計數;結構化存查=未來 L4
(Evolution Agent 讀 lessons 自我進化)的直接糧食。summary 鐵律(inject
守則):technical analyst 心態、引實際路徑/符號/命令、密度優先、誠實
記失敗——取代原始資料。

## 0. 為什麼

原本 agent 完成只回一行 `TASK_DONE` + structured `{reason,status,next}`;Jira comment
只有 grader 驗證結果 + 一句自報,**看不到 agent 到底產了什麼**。人要評分/判完成度時資訊
不足。本設計讓 agent 交付**結構化產出**,並設計好兩個人機表面:**Jira comment** 與
**一次性 HIL 表單頁**。

## 1. 兩層產出(control / data 分離)

呼應 [control/data path 模型](interaction.md#131):

### 1a. structured-output(控制層,CLI 強制)

`contract.CONTRACT_SCHEMA` 由 `claude --json-schema` / `codex --output-schema` 強制,
**每次一定有**。從 `{reason, status, next}` 加一欄 **`summary`**:

```jsonc
{ "reason": "…", "status": "done|failed|need_human|handoff", "next": null|{…},
  "summary": "100–200 字:完成了哪些 item、還沒完成哪些 item(精簡自報)",
  "score":   0-10 }   // agent 對本輪成果的完成度自評(整數)
```

`score` 餵 **HIL 三訊號**(grader / agent 自評 / 人類)+ **`auto_close`**(見下):profile 設
auto_close 時,`human_score` 直接複製 `agent_score`、自動關單。`contract.agent_score()` 取值
(缺/超範圍→None)。

`summary` 是**人看的第一眼**:進 Jira comment 開頭 + HIL 表單頁三訊號之一。CLI 強制 →
即使 agent 忘了寫 OUTPUT.json,至少有這段。

### 1b. OUTPUT.json(資料層,workspace 檔)

Agent 在 workspace 根寫 `OUTPUT.json`(格式由 [inject_claude_md_end.md](../../config/templates/inject_claude_md_end.md)
指示);harness 於終態讀取。**四類分明**:

```jsonc
{
  "summary_md": "<過程與成果的完整 markdown 敘事>",         // 完整版(表單頁渲染成 HTML)
  "code":        [ {"system":"gerrit","url":"https://…/c/proj/+/1234",
                    "ref":"refs/changes/…","note":"改了什麼"} ],   // 程式碼變更(Gerrit)
  "attachments": ["report.md","diagram.png","spec.docx"],   // workspace 相對路徑;要給人的檔
  "references":  [ {"label":"完整資料集","path_or_url":"/data/abs/… 或 https://…",
                    "note":"…"} ]                            // 只給指標、不上傳(大檔/外部/內部絕對路徑)
}
```

- **attachments vs references 的意圖**:放 `attachments` = 「請把這檔交到人手上」;放
  `references` = 「這只是指標,存查用」。agent 用「放哪一欄」表達意圖,harness 不猜。
- 選填:全部欄位皆可缺(降級);`summary_md` 缺則表單頁用 structured `summary` 頂替。
- 形狀可用 [JsonGrader](../../src/arcp/grader.py) 在 profile `verify` 加一步驗
  (`{json:{file:OUTPUT.json, require:[summary_md]}}`),把「產出格式對」變確定性檢查。
- 安全:`attachments` 路徑一律**解析到 workspace 內**(擋路徑穿越);workspace 外/不存在
  → 跳過該檔 + 記 log(不擋流程)。

## 2. 附件(attachments → 人)

終態時,harness 對 `OUTPUT.json.attachments` 的**存在檔案**算總大小:

- **總和 < 6MB** → 逐一 **附到 Jira issue**(`jira_source.add_attachment`,Jira 原生附件);
  comment 列檔名。人在 Jira 直接點。
- **總和 ≥ 6MB** → 不上傳;form_server 出一個 **`/files/<token>` 下載頁**(TTL 綁 HIL
  請求生命週期、非點一次即失效、可挑檔逐一下載),連結**貼進 Jira comment** +
  **鏡像在 HIL 表單頁**。
- token 即 capability(同 HIL 表單一次性連結的安全模型):綁該票、只服務**已宣告的**
  attachment、路徑穿越防護、`Cache-Control: no-store`。
- 冪等:附件上傳是 Jira 寫入副作用 → **at-most-once**(記已上傳標記,重 poll 不重傳)。
- Jira 事實:附件掛在 **issue** 上(comment 不能內嵌任意附件),comment 以檔名 + 連結引用。
  Jira Cloud 預設單檔上限約 10MB;6MB 總和是保守值(可 config 化,見 §6)。

## 3. Jira comment(結構化 ADF)

Jira Cloud comment 是 **ADF**;貼 markdown/html 不會被渲染。comment 由 **harness 自建精簡
ADF**(不塞完整 markdown 敘事——那個放表單頁渲染):

```
[agent] outcome=SUCCESS(attempt 2,累計 $0.03)          ← 標題段
自報:<structured.summary 100–200 字:完成/未完成>        ← 段落
驗證結果:<grader checks>                                 ← 粗體小標 + 項目清單
程式碼:• Gerrit https://…/+/1234(改了什麼)              ← 清單 + 連結
附件:• report.md • diagram.png(已附到本票)             ← 或:大檔 → 下載頁連結
完整敘事 / 評分:<一次性表單頁連結>                       ← 連結
```

用到的 ADF 節點:`heading`/`paragraph`/`text(+strong 標記)`/`bulletList`/`link`/
`codeBlock`。新增精簡 ADF builder(stdlib,零依賴)。

## 4. HIL 表單頁(自足評分駕駛艙)

一次性連結是**給人的 capability URL**(可能手機開、不一定能連內網 dashboard),所以
score_and_close 表單頁要**自足到能完成評分**:

**唯讀脈絡(判斷用)**
- 票 key + 標題 + **Jira ticket 連結**。
- **三訊號**:grader(S/F/U)+ agent 自報(structured summary)+ 你的評分欄。
- **渲染的 `summary_md`**(markdown→HTML;表單頁是我們自己的 HTML,可完整渲染)。
- **交付物**:code(Gerrit 連結)、attachments(<6MB 的直接下載 / 大檔的 `/files/<token>`)、
  references(檔名 + 指標)。
- cost / attempts;**agent session transcript 連結**;**ClearQuest CR 連結**(CQ 格式待補,
  先留欄位;無 CR 則不顯示)。
- 深入 L3 對話 trace → 附一個 **dashboard ticket 頁連結**(能連內網的人再點,不強迫)。

**表單欄位**(沿用 W10.3):human_score、close_decision(關單/續跑/換手)、handoff 欄、備註。

**實作取捨**:交付物內容於**開表單時 snapshot 進 interaction payload**(summary_md / code /
references / attachment metadata:檔名+大小),form_server 讀 payload 渲染;**檔案 bytes**
不進 payload,由 `/files/<token>` 於下載時讀 workspace。form_server 保持輕(讀 DB + 受控
讀檔),快照不可變、可稽核。

## 5. 何時貼 + 流程

**終態一律貼**(SUCCESS / FAILURE / UNKNOWN 都進 HIL(End) 評分,人要看部分成果):

```
attempt 收尾 → grader 判 outcome
  → 讀 OUTPUT.json(有→完整、無→降級只用 structured summary)
  → 算 attachments 大小 → <6MB 附 issue / ≥6MB 備 /files/<token>
  → 貼結構化 ADF comment(結果 + 自報 + 交付物 + 表單頁連結)   [at-most-once]
  → ScoreGate 發 score_and_close:payload 快照交付物 → 表單頁駕駛艙
```

OUTPUT.json 缺:comment 只有 outcome + structured summary + 一行「agent 未產出 OUTPUT.json」;
表單頁仍可評分(用 structured summary)。**降級不擋流程**。

## 6. 觀測 / 設定 / 事件

- 新 journal 事件:`deliverables_posted`(has_output / n_attachments / mode=attach|link)、
  `attachment_uploaded`(name / bytes)。事件字典見 [observability.md](observability.md)。
- config(profile 或 source 層,預設值):`attach_total_limit_mb: 6`、下載頁 `files_ttl_sec`。
- 失敗降級都 log,不擲例外(交付物是加值,不該弄死派工/評分)。

## 7. 落地清單(分階段)

1. **契約 + 讀取**:contract 加 `summary`;新 `arcp/output.py`(schema/讀取/大小/分類/路徑安全);
   inject.md 教兩層格式;tests。
2. **Jira 附件 + ADF**:`jira_source.add_attachment`;ADF builder;dispatcher 終態組交付物 comment
   + 附件 at-most-once;tests(FakeSource)。
3. **下載頁 + 表單頁**:form_server `/files/<token>`;score_and_close payload 快照 + 駕駛艙渲染
   (summary_md→html、下載、連結);tests。
4. **文件**:使用者/管理者/開發者/troubleshooting 手冊 + interaction.md + observability + FAQ +
   CHANGELOG + v1-reverify-checklist(加一步驗交付物)。

## 8. 與既有設計的關係

- G1 契約([contract.py](../../src/arcp/contract.py)):`summary` 是第 4 欄;`status/next` 不變。
- 證據型停止不變:grader 仍是**終審**;OUTPUT.json 是 agent **自報的產出**(可選用 JsonGrader
  把「格式對」變確定性檢查,但不取代 grader 的完成判定)。
- HIL / 一次性 token([interaction.md](interaction.md)):下載頁沿用同一套 capability-URL
  安全模型;交付物快照進 interaction payload。
- a2a 換手([architecture.md §4](architecture.md)):跨票換手的新票也照此貼交付物;base 脈絡
  注入與交付物並存。

## 9. auto_close(profile 收尾政策)

`require_approval`(開跑前門檻)的另一端。profile 欄 `auto_close: off|on_success|all`(預設 off):
- **off**:正常 HIL(End)—— ScoreGate 發 score_and_close 表單、人評分關單。
- **on_success**:只有 SUCCESS 自動關;FAILURE/UNKNOWN 仍進 HIL(異常才找人)。
- **all**:全終態自動關(無人值守)。

自動關(`ScoreGate._auto_close`):**跳過表單** → `human_score = agent_score`(contract.score;
缺則試 self_score_fn,再缺 None)→ `transition("done")` → journal **`closed(by=auto,
outcome, agent_score, human_score)`**。**outcome 保留**(FAILURE 仍算失敗、dashboard 失敗率
照算——auto_close 是「不等人、如實關」,非粉飾)。**不覆寫 handoff**(handoff 非終態 outcome)。
交付物 comment 照貼(事後可查)。`by=auto` 讓稽核看得出這票沒經人審。

用途:週期性/無人值守 job 用 auto_close 的 profile;高風險 profile 維持 off。同一 profile
只能一種行為 —— 需要兩種就開兩個 profile(profile 很便宜)。
