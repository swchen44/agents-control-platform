# DESIGN_lifecycle — profile / template→workspace / 審批門 / assignee=資源開關

> **⚠️ W10 更新(2026-08-08):生命週期改 HIL 模型。** 舊「交人 inactive」+「等待人類
> pending」合併成 **HIL(Middle)**(過程中等人:triage/審批/預算/交人);成功/失敗/未定
> 收斂成 **HIL(End)** 的結果屬性(不再是頂層狀態);人評分後 (A) 續做關票 或 (B) native
> resume+重置額度續跑;`closed` 為概念終點。本文件下述的審批門 / assignee=資源開關機制
> 仍是 HIL(Middle) 的底層實作。完整新狀態機 + agent↔agent 交接(同票換手(next) vs
> 跨票換手(base))見 [architecture.md](architecture.md) 與 `/concepts` 頁。
> W10.1(模型/圖/網頁)、HIL 行為(W10.2,W11+group A 落地)、
> **a2a 交接(W10.3:同票換手(next) + 跨票換手(base),HIL 表單驅動)皆已實作**。

> 使用者 2026-08-04 提出的一套設計,逐項反問(Q1–Q7)+ assignee 生命週期釐清後定案。
> 橫切 F1(資源閘門)/F3(換手)/G1(結構化契約)/A2(冪等)/E3(evict)/N13(killpg→resume)。
> 現況基礎:profiles.py 已有 `workspace_template`/`workspace_folder`/`skills` 伏筆;
> workspace.py `provision()` 以不變 issue_id 為 key(native resume 綁 cwd)。

## 0. 一句話

profile 像 class(含 template folder 預設 skill),每次確定要 fork 就從 template
**複製**出一個 workspace instance(有 lifetime);**起點由審批門把關**(貼計畫→人填參數→
放行才 copy+fork);**assignee 是資源開關**(不在機器人手上就 killpg 釋放 CPU/memory,
回到機器人才 resume)。核心動機:機器 CPU/memory 有限,要管制。

## 1. 核心概念:template(class) → workspace(instance)

- **template folder = class**:profile 指定一個 template folder path,內含預設 skill/
  骨架。現況 `workspace_template` 只支援 `empty`(空建 + 逐個複製 SKILL.md)→ **擴展成
  「整包複製一個 template folder」**。
- **workspace folder = instance**:每次 fork 前從 template **複製**出來,有 lifetime。
  多個 workspace 可同源一個 template。
- profile 自帶 **agent 名字**(即 `profile.name`),用於命名與路由。

## 2. workspace 命名(Q1:可讀前綴 + 不變 id 尾綴)

**硬約束**:native resume(claude `--resume`/codex)綁 cwd,**path 一旦建立就不能變**。
Jira summary 可編輯,**不可入 path**。

```
<root>/tickets/<agent名>__<ticket_key>__<issue_id>/ws/
                 例:  myagent__PROJ-123__10042/ws/
```

- `agent名`(profile.name)+ `ticket_key`(PROJ-123,不變)= 可讀
- `issue_id`(10042,數字、永不變)= 唯一且 resume-safe 的尾綴
- summary 只寫進 `ws/TICKET.md`,不進 path。
- **無票任務**(見 §5):`<agent名>__<run-name>__<timestamp>/ws/`(timestamp 保唯一不變)。

## 3. instance 生命週期:回收(Q2)

- ticket 到終態(Close/SUCCESS)後**保留 `retention_days` 天再回收**。
- **default 270 天**(偏稽核保守,近一年);config 可調。
- 期間可 resume/稽核;過期自動清 workspace 目錄(store 記錄另議,至少留 journal)。

## 4. 起點審批門(Q3/Q4/Q5/Q7)

### 4.1 流程

```
機器人:  match(title keyword / assignee regex)
         → 把 PLAN 寫進 ticket description(§4.2):原始需求上方保留,
           底部加 YAML 標記區塊(空欄 + 簡易 help)
         → 首次貼一則「填表說明」comment(只貼一次,寫過不重複=A2 冪等)
         → assignee 改成該 profile 指定的審批者(人類)
人類:    編輯 description 填參數(agent_name / 其它) → assignee 改回機器人
機器人:  讀 description YAML → 校驗
           ✓ 通過 → copy template → fork claude/codex
           ✗ 有問題 → 把 error message 寫進 comments → assignee 轉回人類(退回重填)
                     → 迴圈,直到通過或超過 max_revisions
```

### 4.2 description 參數區塊(Q4:標記包起的 YAML)

> ★ 2026-08-05 升級為**多方分區段**(human/control/agent:<名>各專屬段、機器段附 hash、
> 區塊置頂 human 前置、開始+結束標記界定、全掃描驗 hash+log、區塊外不碰)。
> 定案規格見 **docs/history/PLAN_wave2「分區段 description 規格」**;實作 sections.py 已按定案版面落地
> (`parse`→(before,sections,after)、`render` 區塊置頂+canonical 序、`verify_and_restore`
> 全掃描+log,14 tests 全綠)。

- **原始需求描述保留在上方不動**。
- 底部加**標記包起的結構區塊**,機器人只讀/寫標記區內,人類只填區內空欄:

```
<原始需求描述…原封不動…>

<!-- ARCP-PLAN v1 (機器人維護;請只編輯下方 value,勿動 key/標記) -->
```yaml
# 填表說明:agent_name 從 [myagent / reviewer / ...] 擇一;param 選填
template: templates/python-fix        # 機器人填,人類可覆寫
agent_name:                           # ← 請填(必填)
param:                                # ← 選填
```
<!-- /ARCP-PLAN -->
```

- YAML 內含**簡易 inline help**(教怎麼填)。
- 解析可靠(標記界定 + YAML 結構),不猜自由文字。

### 4.3 放行 / 觸發(Q3/Q5)

- **放行信號 = assignee 改回機器人**(現成 assignee 監看,不解析文字,不誤判)。
- **per-profile 可選**:`require_approval: true/false` + `approver:`(該 profile 的審批者
  email,不同 profile 可交不同人)。
- **觸發範圍**:綁 `require_approval`,**不綁「首次」**——凡是要 **copy template + fork
  一個新 session**(首次建立 **或** F3 換手重 fork)都審;**純 resume(繼續同 session)不審**。

### 4.4 卡死邊界(Q7)

- **審批中的票 = 零機器資源**:fork 之前無子進程;且「等 ok 才 copy」(lazy),連磁碟都
  不占,只占 Jira 一段 description。每輪 poll 重讀評估=幾個 API call(A3 rate limit 保護)。
- **不硬逾時、不占並發額度**:等審批 = `pending:human`,不消耗 attempt、**不占 F1 並發額度**
  (人審不該被機器催,也不該卡住機器資源)。
- **退回上限**:`max_revisions`(default 3),超過 → **escalate**(標記需人工介入、停自動退回)。

## 5. 任務源:Jira poller + 內部 jobs(scheduled/oneshot)

profile 不只被 Jira 票驅動,還能被**內部 job(觸發器)**啟動。現況(W3.4)= 內部觸發器
用 pseudo-ticket inline 跑、不開 Jira。

### 5.1 統一 job(J1,2026-08-11 定案+實作)

一個 job = **跑一個 `script`**(相對 `config/scripts/<subfolder>/`,執行 **cwd 進該 subfolder**;
log 存 `runs/…/transcript/{stdout.log,stderr.log,run.tgz}`,dashboard 可看可下載——兩種 type
共用 `_run_logged_script`)。`trigger_type` 決定 stdout 怎麼用:

- **script-job**:純做事,stdout 只是 log,**不開票**。
- **agent-job**:stdout **必須是 JSON 任務清單** → 每筆**像人一樣** `create_ticket`
  (**不建 session、不鎖定 profile**)→ 票走 poller 既有 route/triage 流程(所以能享用
  A/B / 條件式選 profile;固定 profile 就讓 route 直接指定)。stdout 非 JSON / rc≠0 →
  `trigger_error`(看該 run 的 `stderr.log`)。

- **排程**:`count`=次數上限(1 單次、0 無上限需 cron、N 個 cron 點)、`cron`/`every`=時機
  (cron 優先);count 省略預設 1;持久化 `run_count`。
- **crid 通道(J2 契約)**:任務可帶 `crid` → agent-job 寫進票 **description 最上面的 yaml**
  (`crid: WCNCR…`,人可讀;只認已知 key `crid`/`prompt`/`email`)→ dispatcher 建 session 時
  `parse_ticket_meta` 讀回 → `session.clearquest_id`(去重 + close→CQ 回寫)。
- **收尾**:job 開的票走正常終態;無人值守就讓對到的 profile 設 `auto_close`(見
  [agent-output.md §9](agent-output.md))——與 job 解耦。

事件:`script_run_started`/`script_run_finished`(兩種 type)、`job_fired`
(`job`/`run_name`/`task_idx`/`crid`,agent-job)。實作:`triggers._run_logged_script` /
`fire_agent_job` / `parse_ticket_meta`、poller `_run_due_triggers`(依 trigger_type 分流);
`tests/test_triggers.py` / `test_jobs.py`。config:

```yaml
outer_loop:
  triggers:
    - name: scan-cq
      run_name: scan-cq
      trigger_type: agent-job
      script: cq/scan.sh         # = config/scripts/cq/scan.sh;cwd 進 cq/
      labels: ['cr']             # 開的票貼此 → 命中 route → triage(不 pin)
      count: 0
      cron: '*/10 * * * *'
    - name: disk-clean
      run_name: disk-clean
      trigger_type: script-job
      script: maint/clean.sh     # 純做事、不開票
      cron: '0 3 * * 1-5'
```
腳本清單與範例見 `config/scripts/README.md` + `config/scripts/example/scan.sh`。

## 6. assignee = 資源開關(使用者核心補充)

**原則:只要 assignee 不在機器人手上,該 agent 就不吃機器資源。**

```
assignee = 機器人(env JIRA_EMAIL) → active   (子進程在跑;占 CPU/memory;占 F1 並發額度)
assignee = 人類                   → inactive (killpg 關子進程;釋放 CPU/memory;讓出 F1 額度)
人類把 assignee 改回機器人          → resume → active(--resume 復活續跑,不重工)
```

- **inactive = killpg 硬關 + `--resume` 復活**(N13/E3 現成)。**不可用 SIGSTOP soft 凍結**
  ——那樣進程還在、仍占 memory,違反「不占 memory」。
- **★ W5.3 實時 killpg 已落地(E3)**:`POST /evict/<issue_id>`(control API)→
  寫 `attempts/EVICT` 檔 → RawCLIAgent evict 看門狗(1s 輪詢)即刻 killpg CLI
  進程組 → envelope `error_kind=evicted` → dispatcher **不消耗 attempt**、
  session 留存 → 下輪 native resume 續跑(e2e 實測:t+8s 觸發、t+9.3s 全結束)。
  ticket 詳情頁有 Evict 按鈕。限制:assignee 交人的「自動」即時 kill 仍受同步
  poll 限制(attempt 期間 poll 阻塞,看不到 assignee 變化)——人工即時 kill
  用 /evict;assignee 語意由下輪 inactive 接手。
- **只釋放運算資源**:workspace 磁碟、store 記錄、session_id 留著(才能 resume)。
- 邊角(交人類→inactive 時):
  1. **孤兒進程** → killpg 殺整個進程組杜絕(N13)。
  2. **關到一半的副作用** → A2 冪等(resume 重放已完成工具,不重複)。
  3. **agent-server 共享 server** → evict 這個 conversation 的子進程 ≠ 關整個長駐 server
     (E3 + server_manager)。
  4. inactive **不動持久化**,只關運算。

**這條原則統一 5 件事**:F1(inactive 不占額度)、F3(換手=assignee 監看)、
N13(killpg→resume)、E3(evict/rehydrate)、A2(關一半靠冪等)。使用者「怕系統不夠用」
從「開工前限流」延伸為「**整個生命週期:不在機器人手上就不吃資源**」。

## 7. 帳號 / token(不新增 token)

- **機器人** = 現有 `~/.env` 的 `JIRA_EMAIL`(swchen.tw@gmail.com)+ `JIRA_API_TOKEN`。
  發 comment / 改 assignee 用它。`~/.env` 在 HOME、不在 repo 樹內,**不會被 commit**。
- **人類審批者**(如 swchen44@gmail.com):harness **不需要它的 token**(人在 Jira 網頁自己
  操作)。只需在 `config.yaml` per-profile `approver:` 記其 email(**非機密**)以判斷
  「這個 assignee / 這則 comment author 是不是本人」。
- 結論:**一個機器人 token 就夠**,不新增。

## 8. profile schema 變更(落地清單)

| 欄位 | 現況 | 變更 |
|---|---|---|
| `workspace_template` | `empty`(空建+注入 skill) | 擴展:template folder path,整包複製 |
| `workspace_folder` 命名 | `tickets/{issue_id}` | `tickets/{agent}__{ticket_key}__{issue_id}` |
| `require_approval` | 無 | 新增 bool(default false) |
| `approver` | 無 | 新增 email(require_approval=true 時必填) |
| `max_revisions` | 無 | 新增 int(default 3) |
| trigger source | 只 Jira poller | 新增 scheduled/oneshot 內部觸發 + run_name |
| `retention_days` | 無(不回收) | 新增 int(default 270) |

## 9. 波次落點

- **W1(F1/G1/A3/A4)**:F1 並發額度需認得「inactive 不占額度」→ §6 的額度語意先進 W1;
  G1 結構化契約與 §4.2 的 YAML 參數同源(schema 一起定)。template 複製 + 命名(§1/§2)+
  無票源(§5)是 provision 基礎,建議 **W1 前置**(fork 前要先能 provision)。
- **W2(F2/C4/F3)**:審批門(§4)+ assignee=資源開關(§6)主體屬 F3 換手 + 監看,放 W2;
  QUEUED 可視化要顯示 `pending:human`/`inactive` 狀態。
- **W3/W4**:retention 回收(§3)、A2 冪等(§6 邊角 2)、E3 evict 對照(§6 邊角 3)。

## 10. 未定的實作細節(我可自行決定,寫 PLAN 時定;有異議再提)

- **match 多 profile 命中**:沿用現有 routing 排序(標籤路由優先於關鍵字啟發式)。
- **template 複製原子性**:複製到臨時目錄再 rename;中途失敗清理,標 infra(不消耗 attempt)。
- **run name 校驗**:限 `[a-z0-9-]`,避免 path 注入;衝突則附後綴。
- **description 併發編輯**:機器人寫標記區前先讀最新版,只換標記區塊(避免蓋掉人類同時的編輯)。
