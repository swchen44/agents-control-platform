# 給離線分析者的除錯導引(AI 與人)

> 你正在讀的這份 repo,很可能是**公司內網裡的一份凍結 snapshot**:不能連外、抓不到
> 更新、也問不到原作者。**所有答案都必須在這份 repo 內。** 這份文件是你(不論是
> AI coding agent 還是工程師)在這個環境裡除錯/分析的起點與守則。

## 這個 repo 是什麼

ARCP 讓 headless coding agent(`claude -p` / `codex exec`)由 Jira 事件驅動、長時間可靠
執行、可觀測、可控制。一句話:**你用既有的 Jira 操作,指揮一支 agent 大軍**。
全貌見 [README](../README.md) 與 [專案檔案介紹](project-overview.md)。

## 離線工作守則

1. **不要假設能連外。** 沒有 pip install 新套件、沒有查線上文件、沒有呼叫外部 API。
   套件相依已在 `uv.lock`;dashboard 元件已全部 vendored 在 `vendor/`。
2. **用離線測試驗證假設,而不是空想或跑真的。** 改任何東西後,先跑離線集(見下)。
   **不要**為了驗證就去跑真 Jira / 真 agent(耗成本、耗電;真依賴的測試不在離線集)。
3. **地面真值是 runtime 證據,不是 agent 自稱。** 判讀一律以 journal + 檔案系統 +
   grader 為準(見「關鍵不變量」)。
4. **不要 wipe runtime store**(`runtime/harness.db`)—— 那是冪等的記憶,
   清掉會讓 open 票被重派重跑(見 [LESSONS #9](lessons.md))。
5. 這份 repo 的**文件就是知識庫**:除錯照下面的路徑走,別重新發明。

## 除錯從哪開始(標準路徑)

```
症狀 → troubleshooting.md(症狀→診斷→處置)
      → observability.md(證據在哪、怎麼讀 journal、事件字典、典型序列)
      → lessons.md(歷史踩坑「症狀→根因→對策」)
      → decisions.md / requirements.md(為什麼這樣設計)
```

- **[troubleshooting](troubleshooting.md)** — 從你看到的現象往下找。
- **[observability](design/observability.md)** — journal 是主要證據軌;§2 有純 stdlib 的
  離線查法,§4 有「正常/失敗/HIL/crash-resume」的典型事件序列,對照就知道票停在哪一步。
- **[lessons](lessons.md)** — 前人踩過的坑,多數 bug 這裡有前例。

## 關鍵不變量(「對的樣子」)

除錯時據以判斷「這是不是 bug」:

- **證據型停止**:`attempt_finished(raw=completed)` 只代表 runner 結束,**不代表任務
  完成**;要 profile 的 `verify`(grader)過才 `resolved(SUCCESS)`。exit code=0 不是完成
  證據(codex SIGTERM 也 rc=0)。見 [decisions D2](decisions.md)。
- **三態 outcome**:SUCCESS / FAILURE / **UNKNOWN**(只有人能解)。別把 UNKNOWN 當
  FAILURE 重試。見 [decisions D3](decisions.md)。
- **envelope 契約跨 backend 不變**:三 backend × 雙引擎共用同一 envelope
  (`src/arcp/contract.py` 的 `CONTRACT_SCHEMA`);換執行單元 dispatcher/grader 零改動。
  見 [decisions D4](decisions.md)。
- **冪等**:agent 層靠 native resume、harness 層靠 store「先持久化再外寫」(at-most-once)。
  見 [設計/冪等](design/idempotency.md)、[decisions D12](decisions.md)。
- **狀態機**:6 態 HIL 模型(todo/running/queued/HIL(Middle)/HIL(End)/aborted),
  success/failure/unknown 是 HIL(End) 的**結果屬性**不是頂層狀態。見
  [設計/生命週期](design/lifecycle.md)。
- **路徑解析一律 repo-root 相對**:`arcp.paths` 向上找 `pyproject.toml` 當錨,解析
  config / vendored / runner —— **不要**在 code 裡用 `dirname(__file__)` 硬推相對位置
  (W12.1 就這樣讓 runner 定位壞掉)。
- **內網零外部依賴**:任何 CDN/外部字型/外部元件都是禁忌(見 [decisions D5](decisions.md))。

## 離線驗證怎麼跑

從 repo root(相依已 `uv sync`;若無 uv,系統 python + `pip install -e .` 亦可):

```bash
uv run ruff check .                                   # lint + import 排序
for t in tests/test_*.py; do uv run python "$t"; done # 單元
uv run python tests/harness_selftest.py               # 路由/config/指令 冒煙
ARCP_CONFIG=routes.example.yaml uv run python tests/e2e_dashboard.py  # dashboard e2e
ARCP_CONFIG=routes.example.yaml uv run python tests/e2e_form.py       # 互動服務 e2e
python3 scripts/gen_event_dict.py --check             # journal 事件字典未漂移
```

以上**全綠 = 離線可信**。需真 Jira/agent 的測試(`scripts/smoke_jira.py`、
`tests/e2e_c*`、`e2e_codex*`)**不在離線集**,除非你確實在有憑證/額度的環境,否則別跑。

> 關鍵檔案地圖見 [專案檔案介紹](project-overview.md);開發細節見
> [開發者手冊](developer-guide.md)。
