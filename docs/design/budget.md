# Budget / Max-Token 管理

> 讓 agent 的 **token / 花費**分層受控:碰到上限進 HIL(不直接燒爆),使用者能在硬上限內
> 自助增額、超硬上限則走管理者。lesson 來源:code review agent 易 token/成本失控
> (alibaba/open-code-review#409 一類)。2026-08-10 定案 + 實作。

## 1. 上限模型(6 限 = 3 scope × 2 metric)

| Scope | soft / hard | 誰能改 | 破限行為 |
|---|---|---|---|
| **per-ticket**(單票) | **soft + hard**(token、usd 各一組 → 4 欄) | soft:使用者自助(≤hard);hard:管理者 | soft→`budget_increase` 表單自助調高;hard→留言通知管理者 |
| **月/agent**(per profile,日曆月) | 單一 hard | 管理者(yaml + hot reload) | 留言通知管理者;pending 等 reload |
| **global**(全站,日曆月) | 單一 hard | 管理者(yaml + hot reload) | 同上 |

- **soft 存 session**(`ticket_session.soft_tokens/soft_usd`,可經表單調高);**hard 每輪
  即時讀 profile**(管理者改 yaml + hot reload 後,該 profile 所有票的上限立刻提高,免逐票
  re-sync)。
- **兩 metric 都量到就都檢查、誰先破誰卡**;只量到一種就用那種(見 §4 per-engine)。
- **global = 全站月度**(整個實例每日曆月的 token/usd 總量,不分 profile),與 月/agent
  同一個 reset 邊界。

## 2. 檢查時機 + 破限流程

**時機**:沿用 `dispatcher._budget_precheck`,在 `while attempts` 迴圈內、**每輪
attempt/resume 前**跑(跑前擋才不多燒)。檢查順序:**per-ticket(hard→soft)→ 月/agent →
全站**,誰先破誰卡 → `pending:budget`(HIL(Middle))。journal `pending` 帶
`scope`(`ticket-soft`/`ticket-hard`/`monthly`/`global`)+ `cost_usd` + `tokens`。

- **per-ticket soft 破** → `_budget_soft_form`:pending + 發 **`budget_increase` 一次性
  表單**(顯示已用/soft/hard 的 token+usd、目前 summary 快照、Jira 連結)。使用者填新
  soft(≤hard)→ `hil._apply_budget_increase` 寫進 `session.soft_*`(clamp≤hard)、解
  pending → 下輪 resume 續跑。
- **per-ticket hard / 月 / 全站 破** → `_budget_block`:pending + 留言「已達 <scope> 上限,
  只管理者能改 config 後 hot reload,本票即自動續跑」(**無自助表單**)。管理者改 yaml +
  `POST /reload` → hard 即時讀到新值 → 下輪 precheck 過 → 自動 resume。管理者事後可調回。

## 3. 設定(config.yaml)

```yaml
outer_loop:
  budget:                          # 全站月度(只管理者能改 + hot reload)
    monthly_max_usd: 500.0
    monthly_max_tokens: 200000000
inner_loop:
  profiles:
    default:
      budget:
        ticket_soft_usd: 1.0       # 單票 soft(破→使用者自助增額 ≤hard)
        ticket_hard_usd: 3.0       # 單票 hard(只管理者能改)
        ticket_soft_tokens: 300000
        ticket_hard_tokens: 800000
        monthly_max_usd: 50.0      # 月/此 agent hard(只管理者能改)
        monthly_max_tokens: 20000000
```

- 全部欄位 **None = 不限**。load 時驗 **soft ≤ hard**(否則 `ConfigError`)。
- 每張票開出來即有 4 個 per-ticket 欄位語意值,**default 從 profile 來**;soft 存 session
  可調、hard 讀 profile 現值。

## 4. token / usd 統計(per-engine)

- **token**:claude / codex 串流的 `usage`(input+output+cache)由 `rawcli/agent._sum_tokens`
  加總 → envelope `tokens` → `session.tokens` 累計 + journal `attempt_finished.tokens`。
- **usd**:claude 直接給 `total_cost_usd`;**codex 可能不給 cost**(只給 token)。
- ⚠️ **CLI 沒有「token/usd 上限」輸入參數**能中途硬停 —— 所以 soft/hard **不是**傳給
  `claude -p`/`codex exec` 的參數,而是 **harness 每輪 precheck 外部卡住**。
- **不可量的 metric 用量讀作 0**,自然不會誤卡(codex 無 cost → usd 用量 0 → 只由 token 卡)。
  這實現了「只量到一種就用那種」。
- **月/global 用量**:掃 journal `attempt_finished` 當月加總(`store._monthly_sum` →
  `monthly_cost`/`monthly_tokens`/`global_monthly_cost`/`global_monthly_tokens`)。

## 5. edge case:需要超過 hard

使用者若真的需要比 hard 更高:表單只能調到 hard。**流程**:①使用者在表單/留言得知已達 hard
→ 通知管理者;②管理者改 profile yaml 的 `budget.ticket_hard_*`(記錄)+ `POST /reload`;
③hard 即時讀到新值 → 使用者再開 `budget_increase` 表單把 soft 調更高 → 續跑;④管理者事後
可把 hard 調回。極少見,故多幾步無妨。月/全站上限同理(但沒有自助 soft,純管理者)。

## 6. sequence chart(soft 破 → 自助增額 → 續跑)

```
Dispatcher ─▶ precheck   : 每輪 attempt 前;used_usd/tokens vs soft/hard(讀 session+profile)
Dispatcher ─▶ store      : soft 破 → pending:budget(scope=ticket-soft)   [pending]
Dispatcher ─▶ Jira       : 發 budget_increase 表單(@mention + 一次性連結)  [hil_requested]
                          (payload:已用/soft/hard token+usd、summary 快照、jira 連結、hard)
人         ─▶ 表單        : 填新 soft(≤hard)送出
form_server─▶ hil        : apply_submission → _apply_budget_increase
hil        ─▶ store      : session.soft_*=新值(clamp≤hard)、解 budget pending  [hil_resumed]
Poller     ─▶ Dispatcher : 下輪 resume 續跑(precheck 用新 soft 過關)
```
> hard/月/全站破:改為留言通知管理者(無表單)→ 管理者改 yaml + hot reload → 自動 resume。

## 7. 觀測 / dashboard

- **journal**:`pending(reason=budget, scope=…, cost_usd, tokens)`、`hil_requested`
  (schema=budget_increase)、`hil_resumed(reason=budget_increase)`、每輪
  `attempt_finished(cost, tokens)`。
- **dashboard**:①**Server 頁**燈號「budget 月用量(最高)」——全站 + 各 profile 月
  cost/tokens 對上限的最高利用率(綠<80%/黃≥80%/紅≥100%/無上限 gray);②**Agent Detail
  頁**「budget 當月用量 vs 上限」卡(逐列全站 + 各 profile);③**單票詳情頁**
  (`/ticket/<key>`)「來源・連結・用量」卡的 **per-ticket 用量 bar**(cost/tokens vs
  soft/hard)。各 Profile 卡列 per-ticket soft/hard 預設。
- **排錯**:見 [troubleshooting](../troubleshooting.md);事件語意見
  [observability](observability.md)。

## 8. 已移除 / 現況

- **移除**舊 `profile.max_budget_usd` / `max_budget_monthly_usd`、description 的 free-text
  `budget_override`、`scoring.collect_budget_override`(未 release,不相容)。
- token 統計依賴 rawcli 串流 `usage`;其他 backend(acp/agentserver)envelope `tokens`
  留 None(該 metric 用量 0,靠 usd 卡)。
