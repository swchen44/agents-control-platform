# Changelog

格式依 [Keep a Changelog](https://keepachangelog.com/),版本依
[Semantic Versioning](https://semver.org/)。

## [Unreleased]

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
