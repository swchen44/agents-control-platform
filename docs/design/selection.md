# Profile 選擇 / 泛化 triage(Q16)

> 首次派工時,決定一張票**實際用哪個 agent profile**。這同時就是**泛化的 triage**:
> 選到 `require_approval: true` 的 profile = 要人放行;選到 `false` 的 = 直接跑。
> 用來做 **A/B 測試**(同族 profile 分流看效果)或**條件式 triage**(依 ticket 內容選)。
> 實作:`src/arcp/selection.py`(`select_profile`);設定欄位 `profile.select`;
> 接線在 `dispatcher.handle` 的**首次派工**分支(見「何時選」)。

## 何時選、選幾次

- 只在 **session 首次建立(`sess is None`)** 時選一次,**pin 進 session**;resume 不重選
  (pin 一次的手法同「同票換手(next)」),避免每輪 poll 換 profile 造成 workspace churn
  → **同一票結果穩定、可重現**。
- route 命中的 profile 是「main」;若 main 有 `select` → `select_profile` 從
  **[main] + candidates**(去重保序)選一個實際 profile → 用它 provision + 建 session。
- **接線**(`dispatcher.handle`):`elif sess is None and profile.select:` → 呼
  `select_profile(ticket, profile, self.profiles)`;選中且 ≠ main 時 journal
  `profile_selected`(`original`/`chosen`/`method`)並改用該 profile 建 session。

## 設定(main profile 上的 `select` 區塊)

`select` 掛在 **main profile** 底下(與 `agent`/`verify`/`loop` 同層)。候選必須是**已定義**
的 profile,且名字**須以 main profile 名為前綴**(同族好管理)。

**method=random(A/B 均勻分流):**

```yaml
inner_loop:
  profiles:
    filechain:                       # main(route 命中的)
      select:
        candidates: [filechain_v2]   # 候選;prefix 須 = 本 profile 名
        method: random               # 從 [filechain, filechain_v2] 均勻隨機
      agent: { ... }
      verify: [ ... ]
    filechain_v2:                    # 候選必須是已定義的 profile
      agent: { ... }                 # 例如只改 model / prompt / verify 做對照
      verify: [ ... ]
```

**method=script(條件式 triage):**

```yaml
    filechain:
      select:
        candidates: [filechain_fast, filechain_careful]
        method: script
        script: 'uv run select.py'   # argv;uvx/npx/.sh/.py 皆可
```

**fail-fast 驗證(load config 時,見 `profiles._parse_select`)**:candidates 非空、
每個候選 `startswith(main 名)`、候選必須已定義、method ∈ {random, script}、
method=script 需有 `script`。任一不符 → `ConfigError`,不讓壞設定上線。

## 選法

### method=random
從 `[main] + candidates` **均勻隨機**挑一個(A/B 分流)。非密碼用途。

### method=script
把下列 JSON 餵給命令的 **stdin**;命令在 **stdout** 印出選中的 profile 名
(取最後一非空行、strip;**必須 ∈ 池**)。可據 description / summary / labels / crid 做
條件式 triage(例:標籤含 `urgent` → 選快版;description 提到 DB migration → 選 careful 版)。

```json
{
  "ticket": {"id","key","summary","description","created","updated","labels"},
  "clearquest": {"crid","title"},
  "original": {"name","yaml"},
  "candidates": [{"name","yaml"}]
}
```
- `yaml` = 該 profile 的來源檔絕對路徑(`Profile.source_yaml`):inline 在主檔的 = `config.yaml`;
  拆到 `config/profiles/<名>.yaml` 的 = 該檔(Q15,per-owner)。腳本可據此讀 profile 細節。
- `clearquest.crid` 目前由 dispatcher 傳入 `None`(= 空字串);待 R9 ClearQuest 觸發源接上才會填。
- 逾時 **60s**(`selection._SCRIPT_TIMEOUT`);stderr 會被 logger 逐行吐出(`[select:<key>]`),
  方便腳本除錯。

## fail-safe(選擇失敗不擋派工)

選擇過程**任何失敗**一律 fallback 回 **main profile**,不擋派工(journal 記 `error`):
- script 逾時 / 無法執行(`OSError`)
- rc ≠ 0
- stdout 回傳的名字不在池內

> 設計原則:選 profile 是「加值」,不能因它出錯就讓票卡住;寧可退回 main 照跑。

## 與 triage(Q7)的關係:一機制同時決定「要不要人 + 選哪個 profile」

現行 triage = per-profile `require_approval`(人放行閘)。Q16 **泛化**它 —— select 選出的
profile 本身就帶了審批屬性,所以選 profile 的同時就決定了要不要人:

- 選到 `require_approval: true` 的 profile → 進審批門,**要人放行**才跑。
- 選到 `require_approval: false` 的 profile → **直接跑**。

因此同一個 select 機制既能做 A/B(同族分流看效果),也能做「依內容自動 triage」:
**全穩定的任務**寫一個 script(或 random 到不帶審批的 profile)即可跳過人類介入;
**高風險任務**讓 script 選到帶審批的 profile,就自動要人核可。

## 怎麼觀測(operator)

- **dashboard 事件時間軸**:該票詳情頁的時間軸會顯示 `profile_selected`(何時、選了誰)。
- **REST API**:`GET /api/v1/tickets`(清單)與 `GET /api/v1/tickets/{ref}`(單票)回傳的
  `profile` 欄 = 該票**實際 pin 的 profile**(即 chosen);單票的 `timeline` 摘要也含
  `profile_selected` 事件。
- **journal**:搜 `runtime/.../events.jsonl` 的 `profile_selected`
  (欄位 `original` / `chosen` / `method`)。
- **注意**:只有選到 ≠ main 時才記 `profile_selected`;若 random 剛好選回 main,票就直接
  用 main 跑、不另記(dashboard/API 看到的 `profile` 仍是 main)。
