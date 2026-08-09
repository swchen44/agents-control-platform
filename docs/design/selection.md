# Profile 選擇 / 泛化 triage(Q16)

> 首次派工時,決定一張票**實際用哪個 agent profile**。這同時就是**泛化的 triage**:
> 選到 `require_approval: true` 的 profile = 要人放行;選到 `false` 的 = 直接跑。
> 用來做 **A/B 測試**(同族 profile 分流看效果)或**條件式 triage**(依 ticket 內容選)。
> 實作:`src/arcp/selection.py`;欄位 `profile.select`;插入點 `dispatcher.handle`。

## 何時、選一次

- 只在 **session 首次建立(sess is None)** 時選,**pin 進 session**;resume 不重選
  (同 `@agent next` 手法),避免每輪 poll 換 profile 造成 workspace churn。
- route 命中的 profile 是「main」;若 main 有 `select` → 從 **[main + candidates]** 選一個
  實際 profile → 用它 provision + 建 session。journal 記 `profile_selected`
  (`original`/`chosen`/`method`),dashboard/journal 可看誰在做 A/B。

## 設定(main profile 上的 `select` 區塊)

```yaml
inner_loop:
  profiles:
    filechain:                       # main
      select:
        candidates: [filechain_v2]   # 候選;prefix 須 = 本 profile 名(同族好管理)
        method: random               # random | script
        # script: 'uv run select.py' # method=script 時的命令(argv:uvx/npx/.sh/.py 皆可)
      ...
    filechain_v2: { ... }            # 候選必須是已定義的 profile
```

**fail-fast 驗證(load 時)**:candidates 非空、每個候選 prefix = main 名、候選必須已定義、
method ∈ {random, script}、method=script 需有 script。

## 選法

- **random**:從 `[main] + candidates` 隨機挑(A/B 均勻分流)。
- **script**:把下列 JSON 餵給命令的 **stdin**,命令在 **stdout** 回傳選中的 profile 名
  (必須 ∈ 池)。可據 description / crid / summary 做條件式 triage。

```json
{
  "ticket": {"id","key","summary","description","created","updated","labels"},
  "clearquest": {"crid","title"},
  "original": {"name","yaml"},
  "candidates": [{"name","yaml"}]
}
```
- `yaml` = 該 profile 的來源檔絕對路徑(`Profile.source_yaml`):inline 在主檔的 = `config.yaml`;
  拆到 `config/profiles/<名>.yaml` 的 = 該檔(Q15,per-owner)。
- **fail-safe**:script 逾時 / rc≠0 / 回傳不在池 → 一律 fallback 回 main(journal 記 error),
  不擋派工。stderr 會被 logger 吐出。

## 與 triage(Q7)的關係

現行 triage = per-profile `require_approval`(人放行閘)。Q16 **泛化**它:同一個 select 機制
既能做 A/B,也能做「依內容自動選 profile」——選到帶審批的 profile 就要人、選到不帶的就
直接跑。**全穩定的任務**寫一個 script(或 random 到不帶審批的 profile)即可跳過人類介入。
