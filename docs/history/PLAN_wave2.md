# PLAN_wave2 — 審批門 + assignee 生命週期 + 控制面/dashboard

> 承 W1(M10 地基)+ DESIGN_lifecycle §4/§6 + 使用者 2026-08-05 的 4 個補充。
> **單線、小步、每步 commit+push。斷線 resume:讀本檔 checklist + git log。**
>
> **W2 比 W1 大,分兩批(每 phase 仍單獨 commit)**:
> - **W2a 審批門 + 生命週期**:W2.1 logging/lint → W2.2 分區段+hash → W2.3 審批門
>   → W2.4 assignee=資源開關 → W2.5 F3 換手
> - **W2b 控制面 + dashboard**:W2.6 REST 控制面(hot reload/graceful)→ W2.7 web dashboard
>
> 現況錨點(已核對):
> - `detail_server.py`:stdlib http.server 只讀頁(read_journal/read_sessions、
>   render_index/render_ticket、暗色 CSS、5s refresh)→ W2.7 擴充它
> - `commands.py ExternalChangePolicy.on_assignee_changed`:現在 assignee 變→
>   pending:external(撤銷授權)→ W2.4 改成 inactive 語義
> - `dispatcher.handle` 開頭 skip 條件**沒含 inactive**(W2.4 補)
> - `store` 已有 queued/queued_at/inactive 欄位 + active_sessions(W1);`gate` 額度
>   語意認得 inactive(W1);G1 `next` 已解析+記錄(W1,實際換手待此)

## 使用者 4 補充 → 落點

| 補充 | 落點 |
|---|---|
| 1 description 分區段 + owner + hash + attach 引用 + 變數命名 | W2.2(sections)+ W2.3(審批門用它) |
| 2 graceful restart / hot reload / REST + web 控制 | W2.6(REST)+ W2.7(web);Q1=REST + 完整 dashboard |
| 3 logging(未來 debug) | W2.1(先鋪,後面都用) |
| 4 ruff/pytest/標準 python 結構(量產另開 repo) | W2.1 baseline;新碼 ruff-clean、測試 pytest-compatible |

## W2 設計決策(新標 W)

| # | 決策 | 理由 |
|---|---|---|
| W10 | 機器區段(control/agent)末尾 `hash:`(sha256 前 12);算前**規範化**(去 hash 行、strip 行尾、統一 \n、去首尾空行);store 存權威版 | Q2:防篡改(不符→還原)+ 幂等(hash 沒變不重寫 description,省 Jira 寫) |
| W11 | 審批門載體 = 分區段 description(control/human/agent:<名>),取代 DESIGN §4.2 單一 YAML 塊 | 補充1:多方協作、各段 read-only、owner 所有權(呼應 v5 欄位所有權) |
| W12 | assignee=資源開關:人類→`inactive=True`、機器人→清 inactive resume;同步架構下 inactive=**阻止下輪 dispatch**(agent 跑完自然釋放),實時 killpg 長駐 agent 留未來異步 | §6;當前 poll-dispatch 同步,無長駐子進程可即時 kill——但「不再拉起」已達「不占資源」 |
| W13 | 控制面 = run_poller **內嵌後台 HTTP 線程**(stdlib,daemon),持 poller 引用;`/reload`=重 load_config(hot)、`/pause`=paused 標誌跳過 dispatch(graceful) | 補充2「簡單有效」;控制要作用於正在跑的 poller 進程,內嵌最直接 |
| W14 | dashboard 擴 `detail_server` 渲染;index 加 queued/inactive/排隊位(F2)+ 總覽 cost/state/失敗率(C4)+ 控制按鈕(fetch POST W2.6) | 復用現成頁;F2/C4/控制面合一(Q1) |
| W15 | Python `logging`(分級、可配 `ARCP_LOG_LEVEL`/log file)與 journal(events.jsonl 稽核)並存 | 補充3;journal=結構化稽核,logging=運維 debug |
| W16 | 新碼 ruff-clean;新測試 pytest-compatible(`def test_*`+assert,仍可自跑);不大重構現有目錄 | 補充4:量產另開 repo,此處尽量做 |

## 分區段 description 規格 ★ 定案 2026-08-05(W2.2 / W11 / W10)

**使用者 2026-08-05 定案調整(sections.py 初版已實作,待按此重構)**:
1. **版面**:ARCP 區塊**整個置頂**(人一打開就填);區塊內順序 human(最前,簡單選項方便填)
   → control → agent:<名>。
2. **界定標記**:開始 `<!-- ARCP:sections v1 -->` + **結束 `<!-- /ARCP:sections -->`**;
   區塊**外**(前後)所有非區段內容**一律不碰**(不只原始需求,任何正常資訊)。
3. **全掃描 + log**:每次讀 description **掃全部機器段驗 hash**(純 python 不花 token),
   不符=被誤寫 → 還原 + **log 記(段名 + 時間)** + comment 提示;human 段永遠尊重。
4. hash:control/agent 段各附;human 無。命名 snake_case;附件 `key: attach:<檔名>`。
5. **human 段 `human_email` 欄(2026-08-05 補)**:人類的 Jira email,agent 轉票給
   人類時的 assignee 來源;**選填,空 → fallback profile.approver**。有填就即時打
   Jira user-search 驗證可解析(`find_account_id`),解析不到當填表錯誤退回;
   審批「退回」仍退 profile.approver(填表本身可能是錯誤來源);email→accountId
   解析套用在所有 assign(approver 也是 email)。轉人類 fallback 鏈:
   human_email → approver → 都無則不改 assignee(不信 agent 自由文字 next.to)。

目標版面:

    <!-- ARCP:sections v1 -->
    ### [ARCP owner=human]
    ```yaml
    agent_name:            # ← 請填(從 reviewer|fixer|… 擇一)
    param:                 # 選填
    ```
    ### [ARCP owner=control updated=<iso>]
    ```yaml
    template: templates/python-fix
    status: awaiting-approval
    ```
    hash: 3f8a1c9e0b2d
    ### [ARCP owner=agent:reviewer updated=<iso>]
    ```yaml
    result: passed
    ```
    hash: a1b2c3d4e5f6
    <!-- /ARCP:sections -->

    <原始需求 + 任何其它資訊 —— 一律不碰>

以下為 W2.2 初版示意(順序 control-first,待調整成 human 前置 + 結束標記):

```
<原始需求…頂部不動…>

<!-- ARCP:sections v1 -->
### [ARCP owner=control updated=2026-08-05T10:00:00Z]
```yaml
template: templates/python-fix
status: awaiting-approval
```
hash: 3f8a1c9e0b2d
### [ARCP owner=human]
```yaml
agent_name:            # ← 請填(snake_case,像變數名)
param:
```
### [ARCP owner=agent:reviewer updated=...]
```yaml
result: passed
log_file: attach:build-log-2026.txt   # 大 log 走 comment 附件,這裡 attach:檔名 引用
```
hash: a1b2c3d4e5f6
```

- **owner**:`control`(agent server)、`human`、`agent:<名>`。
- **命名規範**:段內 key snake_case;跨段引用 `owner.key`(如 `human.agent_name`);
  附件 `key: attach:<檔名>`(檔名由 comment 附件提供)。
- **所有權**:機器段(control/agent)由 server 代寫 + `hash:` 保護;human 段人類自由編輯、無 hash。
- **檢查流程**(每輪 poll 讀 description 後):解析各段 → 機器段重算 hash vs 附帶 hash:
  符=未動;不符=人類誤改 → 用 store 權威版**還原該段** + comment 提示 + journal;
  human 段永遠尊重。機器段 hash 與上次相同 → **不重寫 description**(幂等)。

## Checklist

**Phase W2.1 — logging + lint baseline(先鋪,橫切)**
- [ ] `arcp_harness/logutil.py`:`get_logger(name)`;level 讀 `ARCP_LOG_LEVEL`(預設 INFO);
      可選 log file(`ARCP_LOG_FILE`);格式含 ts/level/name
- [ ] 關鍵路徑改用 logger(poller 每輪摘要、dispatcher outcome、server_manager、gate 決策
      用 DEBUG);print 保留給 CLI 腳本輸出
- [ ] 跑 `ruff check`(若未裝→`pip install ruff` 到 venv)建 baseline;修新碼 warnings;
      加 `ruff.toml`(line-length 88、選規則集)
- [ ] 單元測 `test_logutil.py`(pytest-compatible):level 生效、file handler 寫檔
- [ ] commit+push

**Phase W2.2 — description 分區段 + hash(W10/W11)** ✅ 定案完成
定案版面已落地:human 段前置 + 結束標記 `<!-- /ARCP:sections -->` + 區塊置頂 +
全掃描所有機器段驗 hash 並 log(段名+時間)+ 區塊外(before/after)內容一律不碰
- [x] `arcp_harness/sections.py`:`parse(description)`→`(before, sections[], after)`;
      `render(before, sections, after)`→區塊置頂、canonical 序(human→control→agent);
      `section_hash(body)`(規範化+sha256[:12]);`verify_and_restore(sections, authoritative)`
      →`(restored, violations)`(全掃描、log 段名+時間、human 永遠尊重)
- [x] owner 模型:control/human/agent:<名>;命名校驗(snake_case key);`attach:` 引用解析
- [x] 幂等:機器段 hash 未變 → render 出同一 hash(不觸發寫);approval 首次把原始
      描述沉到區塊下方(after)
- [x] 單元測 `test_sections.py`(14 tests):3-tuple parse、區塊置頂+結束標記、human
      前置排序、before/after 不碰、hash 規範化、全掃描還原/尊重 human/無權威版仍 flag、
      attach 引用、命名校驗 —— 全綠;ruff clean
- [ ] commit+push

**Phase W2.3 — 審批門主體(§4,建在 W2.2 上)**
- [ ] profile 加 `require_approval: bool` + `approver`(email/accountId)+ `max_revisions`(default 3)
- [ ] `arcp_harness/approval.py`:match→寫 control plan 段(status=awaiting-approval)+
      建 human 空欄段 + 首貼填表說明 comment(幂等,寫過不重複)→ assignee 改 approver
- [ ] 放行偵測:assignee 改回機器人 → 讀 human 段參數校驗:通過→ dispatcher 照常
      copy+fork;失敗→ comment 寫 error + assignee 轉回 approver(退回),`revisions++`;
      超 max_revisions → escalate
- [ ] dispatcher 接:require_approval 且首次/換手且未放行 → 走審批門(不 fork);純 resume 不審
- [ ] 單元測 `test_approval.py`(mock source/store):貼計畫→填表通過→放行、填錯→退回、
      超上限→escalate、純 resume 不審
- [ ] commit+push

**Phase W2.4 — assignee=資源開關(§6/W12)** ✅
- [x] `dispatcher.handle` skip 條件加 `or sess.inactive`(審批門條件也加 `not inactive` 保險)
- [x] `ExternalChangePolicy.on_assignee_changed`:assignee≠機器人 → `inactive=True`+comment
      (讓出 F1 額度、下輪不 dispatch);assignee=機器人 → 清 inactive(下輪 resume);
      **pending:approval 除外**(審批流自己用 assignee 當放行信號,不可誤標);
      未配置 bot_account_id → 舊語義 pending:external(向後相容,selftest 不動)
- [x] 機器人身份:`source.bot_account_id` config 可覆寫,否則 run_poller 啟動 `myself()`
      解析一次;比對 t.assignee_id;順接 run_poller 掛上 W2.3 `ApprovalGate`(先前未接線)
- [x] (實時 killpg 長駐 agent = 未來異步;同步架構下 inactive 已達「不再拉起=不占資源」,
      註記於 commands.py docstring)
- [x] 單元測 `test_lifecycle.py`(8 tests):assignee→人類=inactive+讓出額度(active_sessions
      排除)、assignee→機器人=清 inactive+session_id 留存可 resume、人→人不重複留言、
      審批中不誤標、legacy 語義、無 session/終態不管、inactive 期間 dispatcher 不派工 —— 全綠
- [ ] commit+push

**Phase W2.5 — F3 換手(G1 next 驅動)** ✅
- [x] command `@agent next <profile>`:重置 session(session_id/attempts/outcome/pending/
      inactive/queued/approval_revisions 歸零)→ pin 新 profile → workspace 哨值
      `(handoff)`(下輪 health 失敗重 provision 新 instance)→ 下輪經 gate 重新排隊;
      目標校驗(profiles 白名單,無效→comment 拒絕)
- [x] **session pin 優先於 route**(關鍵接線):dispatch 的 profile 每輪由 route 標籤
      重推,換手要生效必須 session.profile 優先 —— dispatcher.handle 與 poller._gate
      額度計算一致採 pin;workspace 重建路徑回存(修 latent bug:原本重 provision 不回存)
- [x] G1 的 `next`(status=handoff):next.kind=agent→自動換手(pin next.to,不 grade,
      下輪重新排隊)、kind=human→assignee 改 next.to + pending:human-decision(不排隊,
      session_id 留存可 resume);目標無效→journal handoff_invalid 當一般失敗;
      A↔B 換手迴圈由 A4 budget 上限擋(cost_usd 跨換手累計不歸零)
- [x] 換手 = 新 session/新 fork → 目標 require_approval 重走 W2.3 審批門(reset 後
      session_id=None+pending=None 自然落入審批門條件)
- [x] 單元測 `test_handoff.py`(7 tests):next 換 profile/無效目標/裸 next 拒絕、
      dispatcher 用 pin 並重 provision 回存、G1 agent 自動換、G1 human 交人、
      換手到審批 profile 重走門 —— 全綠;全套回歸無破
- [ ] commit+push

**Phase W2.6 — REST 控制面(hot reload / graceful,W13)** ✅
- [x] `arcp_harness/control_api.py`:stdlib ThreadingHTTPServer,daemon 線程;持 poller
      引用;預設綁 127.0.0.1(無認證,不可綁公網);port=0 支援 ephemeral(測試)
- [x] 端點:`GET /status`(paused/in_flight/queued/inactive/outcomes/pending 計數 +
      cost 彙總;新增 `store.all_sessions()` 支援)、`GET /health`、`POST /reload`
      (reload_fn 閉包:重 load_config+load_profiles → 更新 loop.routes/jql/concurrency
      + disp.profiles + cmds.profiles;壞 config 回 400 不弄死 poller)、
      `POST /pause`、`POST /resume`
- [x] `poller.paused`:poll_once dispatch 階段跳過(只 watch,不派新工);正在跑的不中斷;
      routes.yaml `control: {host, port}`(load_config 透傳);run_poller 啟動/結束接線
- [x] 單元測 `test_control_api.py`(6 tests,真 HTTP ephemeral port):health、status
      彙總、pause/resume、reload 生效+壞 config 400 後仍活、404、paused 只 watch
      不派工+resume 補派 —— 全綠;全套回歸無破
- [ ] commit+push

**Phase W2.7 — web dashboard(F2 排隊 + C4 總覽 + 控制,W14)** ✅
- [x] 擴 `detail_server` 渲染:index 加狀態徽章(優先序 outcome > pending:* >
      QUEUED #FIFO位置 > INACTIVE > active)+ C4 總覽卡(總 cost、in-flight、
      queued、inactive、pending、SUCCESS/FAILURE、失敗率)
- [x] 控制按鈕:Pause/Resume/Reload → fetch POST 到 W2.6 端點(control API 加
      CORS header,跨 port 可讀回應;離線顯示提示);審批門 ticket 顯示**審批狀態卡**
      (狀態/退回次數/decision 軌跡 —— sections 表單本體在 Jira description,
      store 側無 description,故顯示狀態卡而非 sections 原文)
- [x] 採「獨立只讀頁 + 指向 control_api 的 POST」:dashboard 免 poller 也能看;
      detail_server 預設 port 8787→8788(讓給 control),control_url 由 argv[3]
      / env ARCP_CONTROL_URL 指定
- [x] E2E `e2e_dashboard.py`(免 token,假資料,15 checks):總覽卡數字、FIFO
      排隊位置(queued_at 序)、各徽章、控制列指向、審批卡有無、POST /pause
      契約、CORS —— 全 PASS
- [ ] commit+push

## W2 明確不做(留後續)

- **實時 killpg 長駐 agent**(§6 完整版)→ 需異步/長駐 agent 架構(未來)
- **Jira 自訂欄位**(B2)→ 分區段 description 已達欄位所有權,custom field 留公司環境
- **完整 KPI/人力估算**(C3)→ dashboard 先顯示 cost/失敗率,C3 框架另做
- **標準 python 目錄大重構**(補充4)→ 量產另開 repo;此處新碼合規即可

## W2 里程碑

**M11 = 起點可控 + 生命週期閉環**(W2a):審批門把關起點(分區段 description+hash+填表+
退回)、assignee=資源開關(交人=讓出資源、回機器人=resume)、F3 換手進隊列。
**M12 = 可觀測可操控**(W2b):REST hot reload/graceful + web dashboard(排隊+總覽+控制)。
