# PLAN_C — C 期:RawCLIAgent(OpenHands 骨架 + raw CLI 執行單元)

> 承 B/B+。目標:在 OpenHands SDK 內寫 **RawCLIAgent**(與 ACPAgent 平行的
> AgentBase 子類):**直接 spawn `claude -p`/`codex exec`、解析原生 stream-json、
> 發完整細粒度事件**進 OpenHands event-sourced 體系。事件從 B 的 ~19 回到
> A 級 ~248;不走 ACP,故無 14-vs-248 粒度損失、無 adapter 版本鏈、有中途控制窗口。
> 依 research/2026-08-abc-roadmap-analysis.md(C=C2 RawCLIAgent,非 fork adapter)。
> **單線、小步、每步 commit+push。斷線 resume:讀本檔 checklist + git log。**
> 最後更新:2026-08-03。

## 為什麼 C(承 abc-roadmap §3)

- B 的粗粒度(14 事件、無中途控制窗口)卡在 **ACP 協定 + adapter**,非 OpenHands 本體。
- C2(RawCLIAgent)不 fork adapter、不碰 ACP:在 SDK 內換掉 Conversation 裡的 agent。
- 困難知識已付清:A 路 `examples/jira-agent-poc/arcp_poc/drivers.py` 有 claude
  stream-json / codex --json 的完整解析 + 終止語意 + resume 梯度 + 全部陷阱。
- 收割品帶進 C:B+ 的 detail page(conversation + trace 兩視角)照用,**事件更細**。

## 已釘死的事實

- **C spike 已證(2026-08-03,`spike_rawcli_agent.py` 4/4)**:`Conversation(agent=
  自製 AgentBase 子類)` 接受外部類——**in-process 不用 fork**;`step()` 內
  `on_event(<SDK 事件>)` 進 event 體系;真 `claude -p` 已在 Conversation 內跑通一輪。
- envelope 契約(B/B+ 定型):`completed/session_id/truly_resumed/cost/error`
  ——只要 RawCLIAgent 的 runner 吐同一份,harness dispatcher/grader/三態**零改動**。
- A 路 crash→resume 實測 4/4(claude/codex 2×2);`--session-id` 預指定、
  `--resume`、三段梯度、SIGTERM-rc=0 陷阱——全部現成。

## 前置未知數(gate,C.0 spike 先消滅)

- **UC1(關鍵 gate)agent-server 端能否實例化自製 RawCLIAgent?**
  B+.1 是把 `ACPAgent.model_dump()` 塞進 REST 的 `agent` 欄位、server 端反序列化。
  ACPAgent 是 OpenHands 已知 AgentBase 子類(有 discriminator);RawCLIAgent 是
  **我們自己的類,server 端 import 不到** → pydantic discriminated-union 反序列化
  很可能失敗。決定 C 的架構:
    - 若 server 端可註冊/載入自製 agent(類似 `tool_module_qualnames` 的機制,
      或 PYTHONPATH 注入)→ **C 同時得 A 級細粒度 + B+ 可視化/持久化 = 集大成**。
    - 若不行 → C 只走 **in-process**(spike 已證),B+ 收割改用「journal→detail
      page」路徑(我們的 events.jsonl 本就餵 detail page,不依賴 server)。
  兩條路都可行,gate 只是決定走哪條 —— 先驗再定。

## Checklist

**Phase C.0 — gate spike:server 端自製 agent(UC1)** ✅ 2026-08-03 **PASS**
- [x] `spike_c0.py`:server 啟動時 import 自製 agent 模組(`c0_server_launcher.py`)
      → `POST /api/conversations` 塞 StubRawAgent.model_dump() → **201 反序列化成功、
      跑到 finished、發出 C0_STUB_OK**(G0/G1/G2 全 PASS,免 token)
- [x] **機制釘死**:`resolve_kind(kind)` 只認已 loaded 子類(`__subclasses__()`);
      server 是我們 spawn 的子進程 → 啟動時 import agent 模組觸發
      `DiscriminatedUnionMixin.__init_subclass__` 註冊 → `resolve_kind("...")` 找得到。
      PYTHONPATH 注入模組路徑 + launcher 先 import 再 `runpy` 起 server。
- [x] **架構定案:C 上 agent-server = 集大成**(A 級細粒度 + B+ 可視化/持久化),
      不必在兩者間二選一。in-process 仍是保底(spike 已證)。
- [x] commit+push

**Phase C.1 — RawCLIAgent 最小實作(in-process 保底)** ✅ 2026-08-03
- [x] `arcp_rawcli/agent.py`:`RawCLIAgent(AgentBase)`,`step()` spawn
      `claude -p` stream-json 逐行解析、發 MessageEvent、終止 finished;
      command 移植 A 路 `ClaudeDriver.build_command`(--session-id 預指定備 C.4)
- [x] E2E(`e2e_c1.py`)in-process 跑 filechain:Conversation FINISHED、
      真 claude 建三檔正確(step1=1/step2=12/step3=123)、**A 路 grader PASS**、
      agent 暴露 session_id+cost($0.0315)、5 則 assistant 事件
      (⚠️ 首跑 grader FAIL 是 e2e 的 EXPECTED 打錯 range(1,4)→(1,n+1),
      碼無誤 —— 既有檔案重驗 grade=True)
- [x] commit+push

**Phase C.2 — 細粒度事件映射(搬 A 路 drivers 解析)**
- [ ] 把 `drivers.ClaudeDriver.normalize` 的 stream-json→事件映射接進 step():
      thinking/token delta、tool_use、tool_result、result → OpenHands 事件
- [ ] 事件數回到 A 級(對照 A 的 ~248);detail page conversation 視角更細
- [ ] codex 版(`codex exec --json`)同步(A 路已有 CodexDriver)
- [ ] commit+push

**Phase C.3 — envelope 契約 + 接進 harness(backend=rawcli)**
- [ ] `inner_rawcli_runner.py`(venv 內):跑 RawCLIAgent,吐同一份 envelope
- [ ] inner_runner.py RUNNERS 加 `rawcli`;profile agent `backend: rawcli`
- [ ] E2E:filechain 走 rawcli backend → grader PASS、dispatcher 零改動實證
- [ ] commit+push

**Phase C.4 — crash→resume in RawCLIAgent**
- [ ] `--session-id` 預指定 + `--resume`(A 路知識);envelope truly_resumed
- [ ] fault-injection:midtool kill → resume 續跑不重工(對照 A 矩陣)
- [ ] commit+push

**Phase C.5 — A/B/C 三方對照 + 收割**
- [ ] 同任務同 grader:A(raw supervisor)/ B(ACP)/ C(RawCLIAgent)事件粒度、
      控制窗口、成本、setup 對照 → COMPARISON.md 補 C 欄
- [ ] detail page 展示 C 的細粒度 conversation(vs B 的 19)
- [ ] commit+push

**Phase C.6 — 文件回寫**
- [ ] COMPARISON / abc-roadmap / HANDOFF:C 由「分析/spike」升級「實跑」
- [ ] commit+push

## 里程碑

M5(C.1-C.3)= RawCLIAgent 接進 harness,filechain 走 rawcli backend 端到端。
M6(C.4-C.5)= C 的 crash-resume + 三方對照,細粒度回到 A 級。
最終判定:C 同時達成「A 級細粒度 + B+ 可視化」—— **C.0 gate PASS 已確認可行**。
