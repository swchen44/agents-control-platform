# Changelog

格式依 [Keep a Changelog](https://keepachangelog.com/),版本依
[Semantic Versioning](https://semver.org/)。

## [Unreleased]

### Changed
- **triage(select)結果模型 + 判不出即中止**:select script 的 **stdout 改嚴格 JSON**
  `{"profile":"<名>|notfound","reason":"..."}`(取代舊的「純文字最後一行」)。`profile ∈ 池`
  → 鎖定該 profile 跑(reason→journal);**`notfound` → ABORTED(untriageable)**:寫
  `session.profile=notfound`、`outcome=ABORTED`、journal `aborted(reason=untriageable)`、留言、
  **Jira 轉取消**(`source.cancel_status`,workflow 沒有則優雅退回 done-category);無效名/腳本
  錯/逾時 → fail-safe 回 main。`jira_source.transition` 加 `prefer_status`(按狀態名優先、退回
  category)。`dispatcher` 加 `cancel_status` + `_abort_untriageable`。新事件 `aborted`(共 47)。
  用語:文件不再用「pin」,改「寫入/鎖定 session 的 profile」。
  tests:test_selection 改 JSON、新增 test_triage_abort(6);selection.md I/O 契約 + 決策表、
  architecture §3.1 + developer-guide「狀態是推導的、沒有 state 欄」、operator-guide cancel_status +
  (預留)CQ 回寫。
- **(預留)close→CQ 回寫**:設計定案(所有 close 若 clearquest_id 有值 → 回寫 Jira 連結+結果
  到 CQ),config `cq_writeback` 為擴充點;**尚未接實際 HTTP**(等 CQ URL/欄位)。

### Added
- **泛化 job(P2):週期/單次執行 agent → 開真 Jira 票**。`outer_loop.triggers[]` 加
  `count`(次數上限:1 單次 / 0 無上限需 cron / N 次;預設 1;持久化 `trigger_state.run_count`)
  + `task`(靜態→票 description)/ `task_script`(腳本 stdout JSON 多筆→每筆開一張,如
  「掃 CQ→每張開票」)+ `labels`(對 route)。**agent-job 走 `triggers.fire_agent_job`**:
  `create_ticket` + 預建 pinned session(直接指定 profile、跳過 routing/HIL)→ 票由 poller
  正常派工 → 自動有 HIL/交付物/評分;無人值守配 profile `auto_close`。**script-job 維持
  inline 不開 Jira**。poller `_run_due_triggers` 改:count 上限 + cron/every 時機(count=1
  無排程→首輪立刻)+ 先 bump 再跑(at-most-once)。新事件 `job_fired`(共 46)。
  `tests/test_jobs.py`(14);設計見 [lifecycle.md §5.1](docs/design/lifecycle.md)。
- **profile `auto_close`(P1;jobs 的收尾)**:見下方 Changed。
- **Agent 產出契約 + 人機介面(agent-output,Q1–Q6 決策樹定案)**:agent 完成時**結構化
  回傳產出**,harness 貼回 Jira + HIL 評分頁自足呈現。設計見
  [design/agent-output.md](docs/design/agent-output.md)。
  - **兩層**:structured-output 加 `summary`(100–200 字 完成/未完成,CLI `--json-schema`/
    `--output-schema` 強制)+ workspace `OUTPUT.json`(`summary_md`/`code[]` Gerrit/
    `attachments[]`/`references[]`)。`arcp/output.py` 讀取 + 附件路徑穿越防護 + 6MB 分類。
  - **Jira comment**:`arcp/adf.py` 精簡 ADF builder;`arcp/deliverables.py` 於終態
    (SUCCESS/FAILURE/UNKNOWN)貼結構化 comment,附件 <6MB 附到 issue、≥6MB 走下載頁;
    `jira_source.add_attachment`(multipart)+ `add_comment_adf`。
  - **HIL 表單頁自足駕駛艙**:ScoreGate 快照交付物進 payload;`form_server` 安全 md→html
    渲染 summary_md + code + 附件下載 + references + cost/attempts + Jira/transcript/CQ 連結;
    `/files/<token>` 服務附件(只服務 OUTPUT.json 宣告且在 workspace 內的檔、TTL 綁票)。
  - 新事件 `deliverables_posted`(共 45);inject_claude_md_end.md 教兩層格式。
    tests:test_output(13)/test_deliverables(11)/test_form_output(15);降級不擋流程。

### Changed
- **CLI 全 flag 化 + 一律 `uv run`**:`run_poller.py` 的 minutes/interval 位置參數改成
  `-m/--minutes`、`-i/--interval`(`-m 0` = 無限常駐);`detail_server.py` **移除位置參數與
  `ARCP_DASH_HOST`/`ARCP_CONTROL_URL` 環境變數**(不再相容),改 `--host`/`--control-url`
  等 flag。兩支 help/docstring、7 份手冊 + README 的執行範例一律用 `uv run python scripts/…`;
  `python3 scripts/…` 全數改掉。**測試也用 uv run**:`e2e_dashboard` 的 subprocess 改
  `uv run python … --runtime/--port/--control-url/--host`(移除 `ARCP_DASH_HOST` env)。
  (`ARCP_CONFIG`、`ARCP_LOG_LEVEL` 保留:前者選 config、後者為 `--log-level` 的底層機制。)
- **換手術語統一為「同票換手 / 跨票換手」**(表明 ticket 是同一張還是換新的;全站 + 全文件 +
  程式面向人字串)。內部資料鍵維持 `next`/`base`(穩定、journal 相容),文件首次出現標
  「同票換手(next)」「跨票換手(base)」對照。HIL 表單 `handoff_kind` 下拉改顯示中文
  label(value 仍 next/base:select 支援 (value,label) 分離,`interaction.opt_pairs`);
  `close_decision`/`next_step` 亦給中文 label;summarize 回填顯示 label。dispatcher/hil
  面向人 comment、dashboard `/concepts`、7 份手冊 + design/faq 全部改用新術語。順修
  `decisions.md` D10 一處與實作不符的描述(同票換手並非「保住半成品換大腦」,而是重置
  session+重新 provision)。
- **服務參數化(-h/--help)**:`run_poller.py` 與 `detail_server.py` 改用 argparse。
  run_poller:`minutes=0` → 無限常駐(24h+);`--control-port`/`--form-port`/`--log-level`。
  detail_server:`--port`/`--host`/`--runtime`/`--control-url`/`--log-level`(相容舊式位置
  參數);import 時不再吃 sys.argv(才不擋 -h 與被 import),`__main__` 覆寫 globals +
  `_apply_control()` 重算 CSP/`_CONTROL_JS`(修 CONTROL 覆寫後衍生常數變舊值)。`--log-level`
  等同 `ARCP_LOG_LEVEL`。
- **dashboard 過濾器 regex/一般字串二選一**:過濾列加「🔤 Regex」checkbox(勾=正則不分
  大小寫;不勾=一般字串包含不分大小寫,原行為),套用於 profile/summary/description 三格,
  無效正則標紅、狀態進 URL。REST:`/api/v1/tickets?q=&field=(key/summary/profile/desc/all)
  &mode=(match/regex)`,共用純函式 `detail_server.text_matcher`;回傳帶 filter/filter_error。
- **AB test / 自動選 profile(Q16)文件完整化**:`docs/design/selection.md` 補 random/script
  兩個獨立 config 範例、「怎麼觀測」章節、script JSON 契約細節、fail-safe/triage 關係;
  operator/user/developer/faq 補使用說明與連結。

### Added
- **grader JSON 形狀檢查(C1)**:`grader.JsonGrader` + profile `verify` 新增 `json` 型別
  (`{file, require:[鍵/點號路徑], types:{鍵:型別}}`)—— JSON 檔存在 + 可解析 + 必要鍵
  + 選填型別的務實 shape 檢查(零依賴,非完整 JSON Schema)。build/test/lint 仍用 `cmd`
  型別表達。dispatcher `_grader` 接線、workspace 驗收標準渲染、`tests/test_grader.py`(15 檢查)。
- **dashboard `/db` schema 視圖**:每張表加 schema 面板(PRAGMA table_info:欄名/型別/
  notnull/預設/pk)—— 即使 0 列也看得到全部欄位(最近常加欄位如 `base_ref`,方便 debug);
  新增 `/db/schema/<table>` 端點 + `db_schema()`。資料檢視本就 `SELECT *` 動態列全欄。
- **dashboard `/concepts` 模組架構補全**:加入 W11 HIL 三模組(`interaction`/`hil`/
  `form_server`)到分層架構圖/graph/職責表;`_arch_svg` chip 寬度隨該層模組數自適應(免溢出)。

### Changed
- **`/concepts` a2a 交接說明對齊 W10.3 實作**:原描述「同票=保留半成品換大腦、跨票=人自建
  Jira」已過時 → 更正為「同票換手(next)=重置 session+pin 新 profile+重新 provision(非
  native resume);跨票換手(base)=**系統** create_ticket 建票 + 注入 BASE_ 脈絡」。狀態機
  SVG 本已含 `hil_end→aborted`(base)/`hil_end→running`(next),僅修 docstring。
- **pre-commit hook 更新**:`.githooks/pre-commit` 原檢已消除的 `harness/*.py` → 改檢
  `src/arcp`/`scripts`/`tests` 的 staged `.py`(ruff)+ 動到 code 時補跑
  `gen_event_dict --check`(本機早一步擋事件字典漂移;缺工具則警告放行)。
- **BACKLOG 校正**:加「實作現況對照」表 —— 標明 F1/F2/F3/G1/G2/A3/A4/C1–C4 等已落地、
  B3 改設計、B1/D1/D2/E1/E2/A1/V1 仍待真環境;避免內網凍結版讀者被 2026-08-04 原始規劃誤導。

- **a2a 換手(W10.3)**:HIL(End) `score_and_close` 與 HIL(Middle) `decision`
  表單內嵌 handoff 欄位(`handoff_kind` next/base + `next_profile` 下拉 + `handoff_prompt`);
  人在裁決時把票交給下一棒(下拉候選=載入的全部 profile,由 `ScoreGate.profiles_fn` 注入)。
  **同票換手(next)** = reset+pin 新 profile+`(handoff)` 哨值(鏡像 agent 自發換手);
  **跨票換手(base)** = 系統 `create_ticket` 在同 project 建新票 + 預建 pinned session(`ticket_session.base_ref`
  指回本票 issue_id)+ 本票收 ABORTED(交接非失敗),`dispatcher._inject_base` 於新票首次
  佈建後複製 base 的 TICKET.md/最後 envelope 進 `ws/BASE_<key>/` + human 指示段前置指路
  (一次性,journal `base_injected`)。kind/profile 不完整 → fail-safe 降級續跑原 agent。
  新增事件 `base_injected`(共 44 種)、`handoff` 加 `new_ticket`/`via`。`hil._do_handoff` +
  `workspace.inject_base_context` + `tests/test_handoff_hil.py`(32 檢查)。設計見
  [architecture.md §4.1](docs/design/architecture.md) 與 [interaction.md §14](docs/design/interaction.md)。

### Added
- **三視角操作手冊(Q1)**:新增 [管理者手冊](docs/operator-guide.md)(起停 / 控制面
  pause-resume-reload-evict-recover / 監控 Server 效能頁 / 調設定 / **備份還原 runbook**(Q4)
  / 多實例 / 異常處置 / 安全 / Operator FAQ,收斂 Q2/3/6 現況)。index/README 以「三視角」
  呈現使用者/管理者/開發者三份 MD。
- **workspace common hooks(Q8)**:比照 common_skills —— `config/hooks/<名>/` 庫 + profile
  `common_hooks` 選子集,佈建時整包複製到 `.claude/hooks` / `.agents/hooks`(統一目標解析,
  與 skills 共用 `_copy_bundle`)。加 `config/hooks/`(README + example-hook)、`common_hooks_dir`。
- **效能監控(Q5)**:整合進 dashboard `/server` 頁 —— 8 個紅黃綠燈指標(attempt 失敗率 /
  排隊深度 / 最舊未終態票等待 / evict 次數 / 花費速率 / 錯誤事件 / 系統資源 / journal 大小,
  門檻見 `perf_metrics`)+ per-profile 細節表(attempts/失敗率/平均時長/累計$/最後活動)+
  bottleneck 說明。全用內部資料(journal/store/sysinfo),`perf_metrics` 純函式 + `test_perf.py`。

### Changed
- **config 改名 + profile 拆檔(Q15)**:`config/routes.yaml`→`config/config.yaml`、
  `routes.example.yaml`→`config.example.yaml`(未 release,不做相容;`config_path` 預設改
  `config.yaml`、CI `ARCP_CONFIG=config.example.yaml`、全 doc 引用更新)。profile 可拆到
  `config/profiles/<名>.yaml`(檔名=profile 名、內容=body),`load_profiles` 自動合併主檔
  inline + 拆檔,同名跨檔 fail-fast;`Profile.source_yaml` 記來源 yaml(Q16 script 拿到候選
  的 per-profile 路徑)。`config.example.yaml` 為 CI 自足仍內建 profile。`test_profiles_split.py`。

### Added
- **profile 選擇 / 泛化 triage(Q16)**:main profile 加 `select` 區塊
  (`candidates` + `method: random|script` + `script`);首次派工從 [main+候選] 選一個實際
  profile、pin 進 session(resume 不重選);`method=script` 吃 JSON stdin(ticket/clearquest/
  候選+yaml)→ stdout 回 profile 名,可做條件式 triage;任何失敗 fail-safe 回 main;journal
  記 `profile_selected`。候選 prefix 須 = main 名(fail-fast 驗證)。這同時泛化 triage(Q7):
  選到 require_approval 的 profile=要人、否則直跑。`selection.py` + `test_selection.py`(11 檢查)。
- **人機互動增修(group A)**:HIL 表單加「給 agent 補充指示」自由欄 → 累加寫進 workspace
  sidecar `.arcp_human.md` → `render_ticket_md` 出「人類指示」段(Q10);`@agent hold` 指令
  = 立即 evict(killpg)→ HIL(Middle)+ hold 表單(含 prompt 欄)→ submit 寫指示 + resume 排隊
  (Q11,不耗 attempt;硬殺限制見開發者手冊 FAQ);`ScoreGate.self_score_fn` hook —— 關單
  首發 score_and_close 時取一次 agent 數字自評(Q13,真自評呼叫留 live)。control/data path
  模型定案(prompt=control 主動提示、TICKET.md=data)。`tests/test_group_a.py` 12 檢查。

### Fixed
- **workspace 佈建原子性(A2/#11)**:install 腳本路徑原本非原子(中途 crash → 半殘 ws
  被下次 provision 當「已建」沿用)。改用 `.arcp_provisioned` commit marker:佈建全部成功
  才立;不完整(無 marker 且無 TICKET.md)→ rmtree 重建;既有 ws grandfather 不動。
  釐清後**不建 qm 式 tool-output ledger**(重工:agent 靠 native resume、harness 副作用靠
  at-most-once、HIL 靠一次性 token,A2 目標已達成)—— 見 idempotency.md A2 結論。

### Added
- **trace 完整性自檢(C2,v5 唯一 P1 硬 KPI)**:`scripts/trace_lint.py` 掃 runtime,確認
  每個跑過的 attempt L0–L3 四層證據齊全(completed/error 必須有 envelope+events;UNKNOWN
  依設計可缺不算失敗);缺 → 列出 + rc!=0(審計)。`tests/test_trace_lint.py` 六情境在 CI 驗證。
- **workspace 佈建三能力(docs/design/workspace.md)**:profile `workspace_install`
  (安裝命令 argv,ARCP 附 `<ws> <template>` 兩絕對路徑、cwd=template、stdout/stderr→logger、
  rc 判定)、`common_skills`(從 `config/skills/` 選子集,整包複製)、`inject_md`
  (把 `config/templates/inject_claude_md_end.md` 貼到 CLAUDE.md/AGENTS.md 尾)。skills 與
  md 共用「統一目標解析」(`.claude/*` vs `.agents/*`:都無→建 .claude 側、同 link→一次、
  不同→兩邊);TICKET.md 加 目標 / 驗收標準(由 verify 渲染)/ Jira 連結。附 `config/`
  範例(example_template + example-skill + inject 檔)。新增 `tests/test_workspace_provision.py`
  (12 檢查:目標解析 4 情境 / common skills / inject 冪等 / install rc / TICKET.md 新段)。

### Changed
- **消除 harness/,改分散到專業標準位置**:`config/`(routes*.yaml + templates/ + skills/,
  git 追蹤)、`vendor/`(離線 vendored 資產)、`runtime/`(harness.db/events/workspaces,
  gitignore)。`arcp.paths` 改 `config_path`/`vendor_dir`/`runtime_dir`/`templates_dir`/
  `common_skills_dir`(移除 `harness_dir`),全 consumer(detail_server/transcript/workspace/
  profiles/inner_runner/run_poller/run_trigger)改走之;順修 W12.1 遺留的 workspace/profiles
  `_HARNESS_ROOT` 指到 src/ 的潛在 bug。歷史文件 → `docs/history/`。

### Added
- **研究策展(docs/research)**:把 `research/` 併入 `docs/research/`,並為每個主題加一篇
  「結論 + 比較」策展文章 —— 總體研究、後端 A/B/C 對照、Crash→Resume、Jira 整合設計、
  對照 qm 平台,各附對照表與「對 ARCP 的影響」,與原始 deep-research 長文同放;
  `docs/index` 加 Research 分區。
- **離線內網文件自足(W13)**:為「交付到內網當凍結 snapshot、只能靠 repo 內文件除錯」
  而補的除錯層 —— `docs/ai-debugging.md`(離線工作守則 + 標準除錯路徑 + 關鍵不變量)、
  `docs/troubleshooting.md`(症狀導向 runbook)、`docs/design/observability.md`(證據地圖 +
  **journal 42 事件字典** + 典型事件序列);`scripts/gen_event_dict.py` 掃 code 產生事件字典
  (混合:自動列表 + 手寫語意,`--check` 防漂移已入 CI);`harness/LESSONS.md` → `docs/lessons.md`
  並入 index;CLAUDE.md 指向除錯導引。
- **專業化打包(W12)**:src-layout(`src/arcp/`)、`pyproject.toml`(hatchling,
  Python ≥ 3.10)、`uv.lock`、MIT `LICENSE`;GitHub Actions **CI**(3.10–3.13 矩陣:
  ruff + build + 離線測試)與 **CD**(tag → GitHub Release);`config.example.yaml`
  範例設定;完整 `docs/`(使用者/開發者手冊、專案介紹、需求、決策、FAQ、設計文件)。
- **互動服務(W11,HIL 人機介面)**:一次性 token 受控表單(`need_info` / `decision` /
  `score_and_close`)、`@mention` 通知、表單提交回寫 Jira human 段 + 稽核 comment +
  觸發 resume、`score_and_close` 關單自動轉 Done、Jira 異常降級/恢復(不做 queue)。
  *(程式接線完成;真 Jira 端到端整合測進行中。)*
- **HIL 生命週期模型(W10)**:6 態(todo/running/queued/HIL(Middle)/HIL(End)/aborted);
  dashboard 狀態機圖、分層模組架構圖 + 職責表、node/edge graph、svg-pan-zoom 互動;
  Introduction 頁。
- **觀測(W9)**:UTC 儲存 + 瀏覽器時區在地化、trace 逐事件時間、事件時間軸
  (L3 對話 + 生命週期合一,共用時間軸)。

### Changed
- 生命週期改 HIL 模型:`success/failure/unknown` 由頂層狀態改為 HIL(End) 的結果屬性;
  舊 `inactive`(交人)+ `pending`(等待人類)合併為 HIL(Middle)。
- assignee 改為**恆定=Agent**(不再當資源開關);人機互動改走一次性表單。
- ScoreGate / dispatcher / external 全面改表單化(棄描述種分 / 交人 assign)。

### Fixed
- `transition` 用 statusCategory key `done`(非狀態名 `Done`)—— 真 Jira curl 測抓到。

---

*W1–W8 的完整歷程見 `HANDOFF.md` 與 `docs/history/PLAN_wave*.md`。*
