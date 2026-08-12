# KPI 框架(C3,v5 §10;2026-08-13)

> **一句話**:從 journal+sessions 算**北極星(First-pass Close rate,雙報)**、
> 效率、**制衡**、coverage 四組指標;Dashboard 首頁 KPI 區(7天/30天/全歷史
> 三窗)+ REST `GET /api/v1/kpi?days=N`。實作 `src/arcp/kpi.py`(純函式)。

## 兩個原則(v5 §10.0,不可妥協)

1. **P1 只建基線,不設目標值**——沒有真實資料前設數字,會逼人調鬆 `verify`。
2. **每個效率指標配一個制衡指標**——效率全部可以靠「降低驗證強度」作弊,
   而且作弊短期看起來像進步。

一律**中位數/p90,不用平均**(agent 執行時間是長尾分布)。

## 北極星:First-pass Close rate(雙報,2026-08-13 定案)

**一次到位** = 該票**無人為返工**:沒有 `retry` 指令、沒有評分表單
`continue`(打回續作)、沒有換手(next/base)。系統內部多 attempt **不算**
(Attempts per close 在盯)——first-pass 的語意是「人第一次看就願意關」。

| 版本 | 定義 | 用途 |
|---|---|---|
| **嚴格版** | 一次到位 close ÷ **已 close 總數** | **決策用**(v5:要不要調高併發的唯一依據) |
| 進行版 | 一次到位 close ÷ **到過終態總數**(含還在等評分的) | 趨勢;接近 v5 原文分母 |

為什麼是北極星(v5 §10.1):同時驗三件事——verify 夠不夠強、模型做不做
得動、routing 派對沒派對;任一出問題它都掉。

## 指標對照(v5 → ARCP 資料源)

| 組 | 指標 | 資料源 |
|---|---|---|
| 效率 | Cycle time med/p90(session_created→closed,分) | journal 時戳 |
| 效率 | Attempts per close(med) | session.attempts |
| 效率 | Cost per close med/p90(**含返工**,比 per-resolved 誠實) | session.cost_usd(累計) |
| 效率 | Throughput(近 4 週每週 closed) | journal `closed` |
| **制衡** | 打回率(`hil_resumed(continue)` ÷ 終態)+ retry/handoff 次數 | journal |
| **制衡** | 人評中位(human_score)+ n | session |
| **制衡** | UNKNOWN rate(`pending(unknown)` ÷ attempts)——**勿單看**(壓低它最快的方法是誤判成 FAILURE) | journal |
| **制衡** | Abandonment(ABORTED ÷ 有結果的)+ **abort 原因分布**(M2 abort_reason) | session |
| coverage | Automation coverage(route_matched ÷ new_issue;全歷史計) | journal |

**作弊訊號**(dashboard 警語):First-pass 升但人評/打回率變差 = 在調鬆
verify;UNKNOWN 降但 silent failure 升 = 把不確定性藏起來。

## 沒做的(v5 有、需人工記錄,P1 略)

Layer attribution / MTTD / Human touch time / Escape(reopen)率 /
Ticket 複雜度分布——等有人工記錄流程再議。Trace completeness 由 C2
`scripts/trace_lint.py` 另管(唯一 P1 硬目標 100%)。

## A/B 對照(C6,2026-08-13;使用者定案=手選簡單版)

Dashboard 首頁「A/B 對照」區:勾選 2+ 個 profile → 每欄一個 profile 的
C3 全指標對照表(前端按需 `GET /api/v1/kpi?profile=X`)。

**可比性警語(顯示在表下)**:手選 profile 的對照**非隨機分流**——差異可能
來自任務不同質,僅供參考;樣本小(n<10)差異無意義。**同一 select 家族
隨機分流的腿才是統計可比的真 A/B**(家族自動分組未做,使用者定案簡單版;
要真 A/B:一個 main profile 配 `select: {method: random, candidates: [...]}`,
分流資料在 journal `profile_selected`)。

## 介面

- Dashboard 首頁「KPI · 北極星+制衡」表(三窗;/data 的 `kpi3`)。
- `GET /api/v1/kpi[?days=N][&profile=X]`(OpenAPI 已列)——自動化/報表拉數。
- 時間窗語意:該票**最後活動**落在窗內才計入(coverage 例外,全歷史)。
