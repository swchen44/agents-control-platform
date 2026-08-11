# Profile 選擇 / 泛化 triage(Q16)

> 首次派工時,決定一張票**實際用哪個 agent profile**。這同時就是**泛化的 triage**:
> 選到 `require_approval: true` 的 profile = 要人放行;選到 `false` 的 = 直接跑。
> 用來做 **A/B 測試**(同族 profile 分流看效果)或**條件式 triage**(依 ticket 內容選)。
> **J4 泛化**:`method=script` 可回**任何已定義 profile**(不限同族候選,稱「軸 B」),
> 且 select 可**遞歸**——選中的 profile 若自己也有 `select` 就再跑一層(多層 triage 樹,
> 最多 10 層)。實作:`src/arcp/selection.py`(`select_profile`);設定欄位 `profile.select`;
> 接線在 `dispatcher.handle` 的**首次派工**分支(見「何時選」)。

## 何時選、選幾次

- 只在 **session 首次建立(`sess is None`)** 時選一次,**寫入 session**;resume 不重選
  (鎖定一次的手法同「同票換手(next)」),避免每輪 poll 換 profile 造成 workspace churn
  → **同一票結果穩定、可重現**。**遞歸也算「一次」**:多層鏈在這一次派工內走完,最後
  鎖定葉節點,resume 不重走。
- route 命中的 profile 是「main」;若 main 有 `select` → `select_profile` 逐層選:每層從
  **[main] + candidates**(random)或**任何已定義 profile**(script,軸 B)挑一個;選中者
  若也有 `select` 就再選一層,直到葉節點 → 用最終 profile provision + 建 session。
- **接線**(`dispatcher.handle`):`elif sess is None and profile.select:` → 呼
  `select_profile(ticket, profile, self.profiles, clearquest_id=…)`(J4 起把票的 crid 一併
  帶進);選中且 ≠ main 時 journal `profile_selected`(`original`/`chosen`/`method`/`chain`)
  並改用該 profile 建 session。

## 設定(main profile 上的 `select` 區塊)

`select` 掛在 **main profile** 底下(與 `agent`/`verify`/`loop` 同層)。候選必須是**已定義**
的 profile。**前綴規則依 method 不同**(J4):`random` 候選**須以 main profile 名為前綴**
(同族 A/B 好管理);`script` 候選**選填、不強制前綴**——腳本可回**任何已定義 profile**(軸 B)。

**method=random(A/B 均勻分流):**

```yaml
inner_loop:
  profiles:
    filechain:                       # main(route 命中的)
      select:
        candidates: [filechain_v2]   # 候選;random:prefix 須 = 本 profile 名
        method: random               # 從 [filechain, filechain_v2] 均勻隨機
      agent: { ... }
      verify: [ ... ]
    filechain_v2:                    # 候選必須是已定義的 profile
      agent: { ... }                 # 例如只改 model / prompt / verify 做對照
      verify: [ ... ]
```

**method=script(條件式 triage,軸 B:可回任何已定義 profile):**

```yaml
    filechain:
      select:
        method: script
        script: 'uv run select.py'   # argv;uvx/npx/.sh/.py 皆可
        candidates: [filechain_fast] # script 模式選填(僅作 stdin 提示);腳本不受此限
```

**fail-fast 驗證(load config 時,見 `profiles._parse_select`)**:method ∈ {random, script};
**random** → candidates 非空、每個 `startswith(main 名)`、且必須已定義;**script** → 需有
`script` 命令,candidates 選填(給了才驗已定義、免前綴)。任一不符 → `ConfigError`,不讓壞
設定上線。

## 選法

### method=random
從 `[main] + candidates` **均勻隨機**挑一個(A/B 分流)。非密碼用途。

### method=script(I/O 契約:兩邊都 JSON)

**stdin(JSON)** 餵給命令:
```json
{
  "ticket": {"id","key","summary","description","created","updated","labels"},
  "clearquest": {"crid","title"},
  "original": {"name","yaml"},
  "candidates": [{"name","yaml"}],
  "all_profiles": ["<所有已定義 profile 名>"]
}
```
- `yaml` = 該 profile 的來源檔絕對路徑(`Profile.source_yaml`):inline 在主檔的 = `config.yaml`;
  拆到 `config/profiles/<名>.yaml` 的 = 該檔(Q15,per-owner)。腳本可據此讀 profile 細節。
- `all_profiles`(J4 軸 B):所有已定義 profile 名——因為 script 可回**任何** profile(不限
  `candidates`),這欄讓腳本知道有哪些合法目標可選。
- `clearquest.crid`(J4):dispatcher 已把票 description 頂端 yaml 契約解析出的 `crid`
  (`parse_ticket_meta`)一併帶進。agent-job 開的票**不鎖 profile、會走 triage**,所以 select
  腳本現在**拿得到** crid,可據 CR 內容做條件式選型。
- 逾時 **60s**(`selection._SCRIPT_TIMEOUT`);stderr 逐行吐 logger(`[select:<key>]`)方便除錯。

**stdout(嚴格 JSON)** —— 命令必須印出:
```json
{"profile": "<候選名 | notfound>", "reason": "為什麼(選填,進 journal/comment)"}
```
可據 description / summary / labels / crid 做條件式 triage(例:標籤含 `urgent` → 選快版)。

**解析決策表(J4 軸 B):**

| stdout | 動作 |
|---|---|
| `profile` ∈ **任何已定義 profile**(含候選池外,軸 B) | 採用;若它也有 `select` → 再遞歸一層,否則寫入 `session.profile` 跑(鎖定,resume 不重選);`reason`→ journal `profile_selected` |
| `profile == "notfound"` | **triage 判不出 → ABORTED(untriageable)**:`session.profile="notfound"`、`outcome=ABORTED`、journal `aborted(reason=untriageable, detail=reason)`、留言、**Jira 轉取消**(`source.cancel_status`,workflow 沒有則優雅退回 done-category) |
| `profile` **未定義**(名字不存在) | **fail-safe 回 current**(journal `error`)|
| stdout 非合法 JSON / rc≠0 / 逾時 / crash | **fail-safe 回 current**(journal `error`)|

> 軸 B 重點:「已定義但不在 `candidates`」現在是**有效選擇**(直接採用),只有**真正未定義
> 的名字**才 fail-safe。這讓一個 triage 腳本能把票分派到整個 profile 庫的任一個,而非侷限同族。

### 遞歸(軸 A,多層 triage 樹)

選中的 profile 若**自己也有 `select`**,就再跑一層 —— 形成多層 triage 樹(例:先粗分
`triage → 前端組/後端組`,再細分 `後端組 → 快版/謹慎版`)。`select_profile` 逐層走,直到
葉節點(無 `select`)。**終止條件**(任一即停,鎖定當層 current):

- **葉節點**:選中的 profile 沒有 `select` → 就是最終結果。
- **回自己**:某層選回 current 自己(含 fail-safe 回 current)→ 停。
- **繞圈**:選到鏈上走過的 profile(`chosen in chain`)→ 停在 current(防無限迴圈)。
- **第 10 層截斷**:`_MAX_SELECT_DEPTH = 10`,超過仍未收斂 → 停(記 warning)。
- **notfound**:任一層腳本回 `notfound` → 立即中止整條(ABORTED),不續走。

`meta` 帶 `chain`(走過的 profile 名序列)進 journal,方便在 dashboard 追整條選型路徑。

## fail-safe vs 明確中止(兩者不同)

- **明確 `notfound` → 中止(ABORTED)**:腳本「決定」這票沒有適用 agent → 不跑、Jira 取消。
- **腳本壞掉 / 無效名 → fallback main**:那是**暫時性/設定問題**,不該因它讓真工作被誤關 →
  退回 main 照跑(journal 記 `error`)。

> 設計原則:選 profile 是「加值」,腳本出錯不擋派工(退 main);但腳本**明確說判不出**時,
> 就如實中止(不硬塞 main 亂跑)。

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
  `profile` 欄 = 該票**實際鎖定的 profile**(即 chosen);單票的 `timeline` 摘要也含
  `profile_selected` 事件。
- **journal**:搜 `runtime/.../events.jsonl` 的 `profile_selected`
  (欄位 `original` / `chosen` / `method`)。
- **注意**:只有選到 ≠ main 時才記 `profile_selected`;若 random 剛好選回 main,票就直接
  用 main 跑、不另記(dashboard/API 看到的 `profile` 仍是 main)。
