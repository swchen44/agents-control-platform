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

**Phase W2.2 — description 分區段 + hash(W10/W11)** ✅ 初版 6f9421d
⚠️ 待按定案版面重構:human 段前置 + 結束標記 `<!-- /ARCP:sections -->` + 區塊置頂 +
全掃描所有機器段驗 hash 並 log(段名+時間)+ 區塊外內容一律不碰
- [ ] `arcp_harness/sections.py`:parse(description)→{原始需求, sections[]};
      render(sections)→description 文字;`section_hash(body)`(規範化+sha256[:12]);
      `verify_and_restore(parsed, authoritative)`→(restored_desc, violations)
- [ ] owner 模型:control/human/agent:<名>;命名校驗(snake_case key);`attach:` 引用解析
- [ ] 幂等:機器段 hash 未變 → render 回原文(不觸發寫)
- [ ] 單元測 `test_sections.py`:parse/render 往返、hash 規範化穩定、機器段誤改→還原、
      human 段尊重、attach 引用解析、命名校驗
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

**Phase W2.4 — assignee=資源開關(§6/W12)**
- [ ] `dispatcher.handle` skip 條件加 `or sess.inactive`
- [ ] `ExternalChangePolicy.on_assignee_changed`:assignee≠機器人 → `inactive=True`+comment
      (讓出 F1 額度、下輪不 dispatch);assignee=機器人 → 清 inactive(下輪 resume)
- [ ] 機器人身份:config `bot_account_id`(或 email→myself() 解析);比對 t.assignee_id
- [ ] (實時 killpg 長駐 agent = 未來異步;同步架構下 inactive 已達「不再拉起=不占資源」,註記)
- [ ] 單元測 `test_lifecycle.py`:assignee→人類=inactive+讓出額度(active_sessions 排除)、
      assignee→機器人=清 inactive+可 resume、inactive 期間不 dispatch
- [ ] commit+push

**Phase W2.5 — F3 換手(G1 next 驅動)**
- [ ] command `@agent next <profile>`:重置 session→換 profile→inactive 清→queued 重評(進新隊列)
- [ ] G1 的 `next`(status=handoff):next.kind=agent→自動觸發換手(換 next.to profile)、
      kind=human→assignee 改人(pending:human,不排 agent 隊列)
- [ ] 換手 = 新 session/新 fork → 若目標 profile require_approval,重走 W2.3 審批門
- [ ] 單元測 `test_handoff.py`:@agent next 換 profile 進隊列、next=agent 自動換、next=human→pending
- [ ] commit+push

**Phase W2.6 — REST 控制面(hot reload / graceful,W13)**
- [ ] `arcp_harness/control_api.py`:stdlib http.server,run_poller 起 daemon 線程;持 poller 引用
- [ ] 端點:`GET /status`(JSON:paused、in-flight、queued、cost 彙總)、`GET /health`、
      `POST /reload`(重 load_config+profiles→更新 poller.routes/dispatcher.profiles/concurrency)、
      `POST /pause`(poller.paused=True)、`POST /resume`
- [ ] `poller.paused`:poll_once dispatch 階段跳過(只 watch,不派新工);正在跑的不中斷
- [ ] 單元測 `test_control_api.py`:reload 換 config 生效、pause 後不 dispatch、status JSON 正確
- [ ] commit+push

**Phase W2.7 — web dashboard(F2 排隊 + C4 總覽 + 控制,W14)**
- [ ] 擴 `detail_server` 渲染(或合進 control_api):index 加 queued/inactive 狀態徽章 +
      排隊位置(FIFO 序)+ 總覽卡(總 cost、各 outcome 計數、失敗率、in-flight/queued 數)
- [ ] 控制按鈕:Pause/Resume/Reload → fetch POST 到 W2.6 端點;審批門 ticket 顯示 sections
- [ ] 與 run_poller 同進程(control_api 服務頁+API)或獨立只讀頁 + 指向 control_api 的 POST
- [ ] E2E `e2e_dashboard.py`(免 token,假資料):頁面渲染排隊/總覽、控制按鈕打通 API
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
