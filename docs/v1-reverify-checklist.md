# V1 付費複驗 —— 一步步照著跑

> **目的**:離線 CI 蓋不到「真 agent 派工」那條路。這份是**真派幾次工**才驗得到的路徑
> 清單,讓你有 agent + 充電時**照著打勾**。免費(唯讀/純本機)部分先用
> `uv run python scripts/reverify_v1.py` 跑掉,這份只列**付費部分**。
>
> **成本**:全程用 `model: haiku` 的測試 profile,約 **$0.1–0.3**、10 幾張測試票。
> **⚠️ 一定在充電時做**(不用 caffeinate;睡眠假 stall 見 [troubleshooting](troubleshooting.md))。
> **在測試 project / 測試票上做**,別動生產票。

每步格式:**做什麼 → 預期 journal 事件 → 在哪看 → ☐ 打勾**。事件語意見
[observability](design/observability.md);排錯見 [troubleshooting](troubleshooting.md)。

---

## Step 0 — 環境準備(只做一次)

1. `~/.env` 有 `JIRA_BASE_URL / JIRA_EMAIL / JIRA_API_TOKEN`(見 [使用者手冊](user-guide.md))。
2. 免費複驗先綠:
   ```bash
   uv run python scripts/reverify_v1.py            # 免費本機 + Jira 唯讀
   ```
   ☐ 印出「免費檢查:N 過 / 0 失敗」。
3. 準備測試用 `config/config.yaml`(從 `config.example.yaml` 改)。**為了驗 handoff / select,
   至少要有兩個 profile**,且都用便宜 model。範例(在 `inner_loop.profiles` 下):
   ```yaml
   default:
     goal: '完成 ticket 描述交付的任務並通過驗證'
     budget: {ticket_soft_usd: 0.3, ticket_hard_usd: 0.5}
     workspace: {template: empty, folder: 'tickets/{agent}-{issue_id}'}
     agent: {backend: rawcli, engine: claude, model: haiku, os_sandbox: true,
             sandbox: workspace-write, timeout_sec: 300}
     verify:
       - {name: task-done, files: {DONE.md: }}
     loop: {max_attempts: 3, on_unknown: pending}
   default_v2:                         # 第二個 profile:給 handoff / select 當下一棒
     goal: '接手並收尾'
     workspace: {template: empty, folder: 'tickets/{agent}-{issue_id}'}
     agent: {backend: rawcli, engine: claude, model: haiku, os_sandbox: true,
             sandbox: workspace-write, timeout_sec: 300}
     verify:
       - {name: task-done, files: {DONE.md: }}
     loop: {max_attempts: 3, on_unknown: pending}
   ```
   `outer_loop.form.mention_account_id` 填**你自己的 accountId**(HIL 表單 @mention 你);
   `outer_loop.form.base_url` 設成你瀏覽器連得到的位址(手機測要用主機 IP、非 127.0.0.1)。
   ☐ 兩個 profile 都在、model=haiku、mention_account_id 已填。
4. 起服務(兩個終端;**一律 uv run**):
   ```bash
   uv run python scripts/run_poller.py -m 30 -i 15        # 終端 A(30 分時間盒)
   uv run python scripts/detail_server.py --host 127.0.0.1  # 終端 B(dashboard :8788)
   ```
   ☐ 終端 A 印 `adopted N pre-existing ticket(s)` + `control API on …:8787` + `form service on …`。

> **怎麼看證據**:① dashboard `http://127.0.0.1:8788` → 點該票進 **ticket 頁**看四層 trace +
> 事件時間軸;② journal 直接 grep(在 `runtime/`):
> ```bash
> cd runtime
> uv run python -c "import json;[print(e['type'],{k:v for k,v in e.items() if k not in('ts','type','issue_id','key')}) for e in map(json.loads,open('events.jsonl')) if e['key']=='SCRUM-XX']"
> ```

---

## Step 1 — 基本派工 + 證據迴路(runner spawn / envelope)

**做什麼**:在測試 project 建一張票、summary 寫「建立 DONE.md 檔」、**加 label `agent`**(對到
example route)。等一輪 poll。

**預期 journal**:`route_matched` → `session_created` → `attempt_started` →
`attempt_finished`(`raw=completed`、有 `envelope`、`cost>0`)→ grader 過 → `resolved`
→(ScoreGate)`score_requested`。

**在哪看**:ticket 頁四層 trace(L0 生命週期 / L1 envelope / L2 attempt log / L3 對話)都齊;
workspace `runtime/tickets/<…>/ws/` 有 `DONE.md`。

- ☐ 出現 `session_created → attempt_started → attempt_finished(raw=completed)`
- ☐ attempts 目錄有 `a1.envelope.json`(L1 證據)
- ☐ grader 過 → `resolved`(帶 `cost_usd` / `human_minutes_saved`)
- ☐ ticket 頁四層 trace 齊(可另跑 `uv run python scripts/trace_lint.py runtime`)

---

## Step 2 — W15 workspace install(佈建腳本 + 原子性)

**做什麼**:給一個 profile 設 `workspace.install`(如 `install: 'uv run install.py'`,template 內
放一支 install.py),派一張票給它。

**預期**:workspace 有 install 產物;poller 終端 logger 有 `[install]` 輸出;佈建全部成功才寫
`.arcp_provisioned` marker。

- ☐ workspace 有 install 產物 + logger 出現 `[install] …`
- ☐ `runtime/tickets/<…>/ws/.arcp_provisioned` 存在
- ☐ **原子性**:install 跑到一半 `Ctrl-C` 殺 poller,再起 → **不會**沿用半殘 ws(會重建;
  因無 marker 且無 TICKET.md)

---

## Step 3 — Q16 profile 選擇(A/B 測試 / 自動選 profile)

**做什麼**:main profile 加 `select`(見 [selection.md](design/selection.md)):
```yaml
default:
  select: {candidates: [default_v2], method: random}   # 或 method: script
  # …其餘同上
```
`POST /reload` 或重起,建一張新測試票(label `agent`)。

**預期 journal**:`profile_selected`(`original=default`、`chosen=default或default_v2`、`method`);
`chosen` 就是實際跑的 profile,且 寫入 session(resume 不重選)。

- ☐ journal 有 `profile_selected`(random 剛好選回 main 時**不發**,屬正常 → 多開幾張看到分流)
- ☐ dashboard 該票的 profile 欄 = chosen;`/api/v1/tickets` 也是 chosen
- ☐ (選配)`method: script` 版:腳本依 ticket 內容選 profile(stderr 以 `[select:<key>]` 記錄)

---

## Step 4 — Q11 指令台 `hold`(強制中斷 → HIL → 續跑)

**做什麼**:在**正在跑**的測試票,開 description control 段的**指令台**連結 → 填 email → 按
`hold`(在該票狀態=running 時可用)。

**預期**:立即 evict(killpg)→ 開 hold 表單(@mention 你 + 一次性連結);你開連結填「給 agent
的補充指示」送出 → 寫進 workspace 人類指示段 → resume 排隊續跑(**不耗 attempt**)。

**預期 journal**:`evicted` → `hil_requested(schema=hold)` →(你填)→ `hil_submitted` →
`hil_resumed`。

- ☐ 留言後很快出現 `evicted`(`count` +1)+ hold 表單連結 comment
- ☐ 填表送出 → `hil_resumed`;`runtime/tickets/<…>/ws/.arcp_human.md` 有你填的那行
- ☐ 下輪 `TICKET.md` 的「人類指示(累加)」段含你填的內容 → agent 續跑

---

## Step 5 — Q10 HIL 表單自由 prompt 欄(補充指示落地)

**做什麼**:任一 HIL 表單(need_info / decision / score_and_close / hold)填「給 agent 的補充
指示」自由欄送出。(Step 4 的 hold 表單即含此欄,可一起驗。)

**預期**:內容累加寫進 `ws/.arcp_human.md`(帶時間)→ 下輪 `render_ticket_md` 出「人類指示」段。

- ☐ `ws/.arcp_human.md` 有 `- [時間] 你填的內容`(可累加多行)
- ☐ 下輪 `TICKET.md` 含「## 人類指示(累加,請一併遵循)」段 + 你的內容

---

## Step 6 — Q13 agent 數字自評(關單時取一次)

> **預設 `self_score_fn=None` → score_and_close 表單的「agent 自評」顯示 `—`**(不擋流程)。
> 要驗真自評:在 `scripts/run_poller.py` 把 `ScoreGate(…, self_score_fn=None)` 換成一個
> best-effort 實作(resume + prompt 問 agent 一個 0–10 數字),再跑一張票到終態。

**預期**:關單首發 score_and_close 表單時**呼叫一次** self_score_fn;表單 context 顯示三訊號:
`grader`(S/F/U)+ **agent 自評 0–10** + 人類評分欄。

- ☐ 預設(None):表單顯示 `agent 自評=—`(正常)
- ☐ (選配)接上 self_score_fn:表單顯示 agent 給的數字 + 只呼叫一次(非每 attempt)

---

## Step 7 — W10.3 同票換手(next,HIL 表單驅動)

**做什麼**:一張票跑到終態(SUCCESS/FAILURE)→ 收到 score_and_close 一次性連結 → 開表單 →
「下一步」選 **換手**、「換手種類」選 **同票換手**、「下一棒 profile」選 `default_v2`、填交接指示 → 送出。

**預期 journal**:`handoff`(`kind=next`、`via=hil`、`to=default_v2`)→ 下輪**同一張票**由
`default_v2` 重新排隊接手(session 重置、workspace 重新 provision)。

- ☐ journal 有 `handoff(kind=next, via=hil, to=default_v2)`
- ☐ 同一張票下輪的 attempt 用 `default_v2` 跑;`TICKET.md` 描述含你填的交接指示
- ☐ 也可改用指令台 `next default_v2` 驗指令式同票換手(journal `handoff kind=command`)

---

## Step 8 — W10.3 跨票換手(base,系統另開新票)

**做什麼**:同 Step 7 開 score_and_close 表單,但「換手種類」選 **跨票換手**、下一棒選
`default_v2`、填交接指示 → 送出。

**預期**:系統 `create_ticket` 在同 project 開一張**新票**(summary 帶 `[base:<原票>]`、沿用原票
labels)交給 `default_v2`;**原票收 ABORTED**(交接,非失敗);新票下輪首次佈建時注入 base 脈絡。

**預期 journal**:原票 `handoff`(`kind=base`、`new_ticket=SCRUM-N`、`via=hil`)+ `outcome=ABORTED`;
新票 `base_injected`(`base=原票`)→ `session_created` → 照常跑。

- ☐ 冒出一張新票 `[base:<原票>]`(label 同原票、被 poller 撿起)
- ☐ 原票 journal 有 `handoff(kind=base, new_ticket=…)`、狀態變 **撤銷/交接(ABORTED)**
- ☐ 新票 journal 有 `base_injected`;新票 `runtime/tickets/<…>/ws/BASE_<原票>/` 有原票的
  `TICKET.md` + 最後 envelope;`TICKET.md` 人類指示段有「先讀 BASE_ 前輪脈絡」指路
- ☐ (fail-safe)表單選換手但**不填**種類/profile → 改成續跑原 agent(journal `handoff_invalid`)

---

## Step 9 — retry 計數穩定性(C3/C5 flaky 複驗)

**做什麼**:故意讓票**第一次失敗、第二次過**(例:先建缺 DONE.md 的任務,agent 補上),觀察
retry 情境下 attempt 計數。

**預期**:`attempt_started(attempt=1)` → 失敗 feedback → `attempt_started(attempt=2)` → 過 →
`resolved(attempts=2)`。計數**穩定不亂跳**。

- ☐ attempt 編號連續(1,2,…)、無重複/跳號
- ☐ `resolved` 的 `attempts` = 實際嘗試次數(對照 memory:`e2e-commands-c3-c5-flaky`,
  若真環境重現不穩,記下 journal 片段供分析)

---

## Step 10 — Agent 產出 / 交付物(OUTPUT.json → Jira + 表單頁)

**做什麼**:讓測試 profile 的任務**產出檔案**(在 workspace 寫 `OUTPUT.json` + 幾個檔;
inject 守則已教格式)。跑到終態。試兩種:小檔(總和 <6MB)與大檔(≥6MB)各一張票。

**預期 journal**:`deliverables_posted`(`has_output=true`、`mode=attach`(小)/`link`(大)、
`n_attachments`);structured 的 `summary` 進 comment 開頭。

- ☐ structured `summary`(100–200 字 完成/未完成)出現在 Jira comment 開頭
- ☐ 有 OUTPUT.json → 多一則**結構化 comment**(自報 + Gerrit 連結 + 附件段)
- ☐ 小檔:附件**直接出現在 Jira 票的附件區**(mode=attach)
- ☐ 大檔:comment/表單頁有**下載連結**;開表單頁 `/form/<token>` → 交付物駕駛艙可下載
  (`/files/<token>?f=…`);越界/不存在的附件被跳過(`skipped`>0)
- ☐ 表單頁渲染 `summary_md`(markdown→HTML)+ code + 附件 + cost/attempts + Jira 連結
- ☐ 沒寫 OUTPUT.json 的票 → 降級只有 structured summary(`has_output=false`),流程不擋

## 收尾

- 全部打勾 → V1 付費路徑在真 agent 下如預期。把有疑點的步驟的 journal 片段留存
  (`runtime/events.jsonl` 對應區段 + `runtime/tickets/<id>/`)供離線分析。
- 測試票可關掉(Done)或指令台 `cancel`;測試 workspace 由 retention 自動回收。
- 看不到某項時:先 `uv run python scripts/trace_lint.py runtime` 檢查四層證據齊不齊,
  再對照 [observability 事件字典](design/observability.md) + [troubleshooting](troubleshooting.md)。
