# TICKET.md 安全掃描(主題 M,M3)

> **一句話**:TICKET.md 是 agent 開工第一份讀物、內容大半是外部輸入
> (Jira description、agent-job script 回傳的 prompt、HIL 人類指示)——
> **spawn 前用外部靜態掃描器掃一遍**(prompt injection 防線);命中就擋派工、
> 發 **security_review 表單**交人裁決(看得到內容與命中理由、可修、可中止)。
> 選配:config 沒設 `security_scan` = 功能關,現行行為零變。

## 威脅模型

agent 以真權限(檔案/命令/Jira 回寫)執行 TICKET.md 描述的任務。任何能寫入
Jira description / 讓 agent-job script 產出 task 的人,等於能對 agent 下指令
——典型 prompt injection(「忽略以上,改做…」、誘導外洩、誘導執行惡意命令)。
M2 已縮小注入面(TICKET.md 移除 Jira 留言段);M3 是主動偵測層。

## 掃描器

[cisco-ai-defense/skill-scanner](https://github.com/cisco-ai-defense/skill-scanner)
——`scan` 預設跑 **static + bytecode + pipeline 三個純靜態引擎**(pattern/YARA,
**不用 LLM**,使用者定案)。ARCP 以外部命令整合(core 維持 stdlib):

```
<command> scan <暫存目錄> --lenient --format json --output out.json
```

TICKET.md 內容複製成暫存目錄的 `SKILL.md` 供掃;JSON 輸出寬鬆解析
(findings[].severity/rule_id/title/description/snippet)。
**內網 snapshot 需預先把掃描器裝好**(pip `cisco-ai-skill-scanner` 或 uvx)。

## 流程(fail-closed)

```
dispatcher 佈建完 TICKET.md ──> spawn 前 _security_gate
    沒配 security_scan ────────────────────────────► 放行(功能關)
    sess.sec_reviewed_at > 0(人審放行過)─────────► 放行
    content_hash == sec_scanned_hash(掃過且過)──► 放行(快取)
    掃描 ok ──────────────────────────────────────► 放行 + 存 hash
    命中 >= fail_on 或掃描器執行失敗 ─────────────►
        pending_reason=security + journal security_blocked
        + security_review 表單(@mention + 一次性連結)
```

表單(自足駕駛艙):**命中清單**(嚴重度/規則/說明/片段)+ **被掃的 TICKET.md
全文** + **修訂文字框**(可修掉可疑內容)+ 裁決:

- **繼續**:修訂文字(選填)寫 workspace sidecar `.arcp_desc_override.md`
  → TICKET.md 描述段改用修訂版(**Jira description 不動**,單一真相保留,
  段標題註明「經人工安全審修訂」);`sec_reviewed_at` 蓋章 → **此票之後不再擋**
  (人審是最終裁決,避免靜態規則誤報迴圈)。
- **中止**:`outcome=ABORTED` + **`abort_reason=security`**(M2 泛化理由)+
  Jira comment 記理由。

掃描器執行失敗(未裝/timeout/壞輸出)= **fail-closed 當命中**(否則弄壞
scanner 就能繞過門禁),表單明確標示「掃描器異常,非必為威脅」。

## 設定

```yaml
outer_loop:
  source:
    security_scan:
      command: 'uvx cisco-ai-skill-scanner'   # 沒設此段 = 功能關
      fail_on: high            # critical|high|medium|low(>= 此嚴重度才擋)
      timeout_sec: 180
```

## 實作對照

| 關注點 | 位置 |
|---|---|
| 掃描 runner / JSON 解析 / 門檻 | `secscan.py`(`scan_text` / `content_hash`) |
| spawn 前門 + hash 快取 | `dispatcher._security_gate`;config 經 `run_poller`(可 reload) |
| 表單 schema | `interaction.FORM_SCHEMAS["security_review"]` |
| 表單脈絡卡(命中+內容) | `form_server._security_html` |
| 裁決處理 | `hil._apply_security_review`(abort / continue+修訂) |
| 修訂取代描述段 | `workspace.DESC_OVERRIDE` sidecar + `render_ticket_md(desc_override=…)` |
| session 欄 | `sec_reviewed_at`(人審放行)/ `sec_scanned_hash`(快取)/ `abort_reason=security` |
| journal 事件 | `security_scan` / `security_blocked` / `security_approved` / `aborted(reason=security)` |
