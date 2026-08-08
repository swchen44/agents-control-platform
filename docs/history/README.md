# 開發歷程(History)

這裡是**過程紀錄** —— 各波(wave)的實作計畫與真 Jira 實測記錄,保留下來讓拿到這包
code 的人能看見「當時怎麼切、為什麼這樣做」。**結論性的東西已在別處**:決策見
[decisions](../decisions.md)、研究與對照見 [research](../research/README.md)、踩坑見
[lessons](../lessons.md)、現況總覽見專案根的 `HANDOFF.md`。這裡是它們的**原始過程稿**。

## 路線期(A/B/C 三條後端路線的計畫)

專案先驗證「三條後端路線」再收斂到主線(結論見 [research/backend-abc](../research/backend-abc.md)):

- [PLAN_B.md](PLAN_B.md) — 路線 B(OpenHands-ACP):最快跑通、白撿可視化/併發,但吃 ACP 粗粒度。開發 checklist + 環境事實(Jira key=SCRUM 等)。
- [PLAN_Bplus.md](PLAN_Bplus.md) — B+ 強化(agent-server、cost 修正、detail page 起步)。
- [PLAN_C.md](PLAN_C.md) — 路線 C(RawCLIAgent):繞開 ACP、原生保真 + 乾淨語意,**最終主線**。
- [PLAN_concurrent.md](PLAN_concurrent.md) — 併發模型(agent-server 一進程管 N conversation)。

## 波次期(W1–W7:從研究進入分波實作)

每波獨立 commit、單獨驗收。**為什麼這樣切**:先地基(資源/契約),再可視化/換手,再閉環/值班,最後證據強化/隔離 —— 依賴由下往上,每波可獨立見效。

| 波 | 主題 | 為什麼這波 |
|---|---|---|
| [W1](PLAN_wave1.md) | 地基:provision、限速、budget、G1 契約、F1 資源閘 | 先把「資源受控 + agent↔harness 契約」打穩,後面換手/派工都建在上面 |
| [W2](PLAN_wave2.md) | 分段+hash、審批門、assignee=資源開關、換手、REST 控制面、dashboard | 讓人能看、能控、能審批;起點閘門 + 換手是生命週期骨架 |
| [W3](PLAN_wave3.md) | codex 第二引擎、冪等分層、retention、觸發源、KPI、隔離介面 | 雙引擎對等 + 冪等(不重花錢)+ 無票排程 + 省人時量化 |
| [W4](PLAN_wave4.md) | transcript 可視化閉環、快照、script trigger 萬用化 | 把「完整飛行記錄器」接起來,人能事後重播 |
| [W5](PLAN_wave5.md) | sid 預派冪等、evict/killpg、六格矩陣、**rawcli 脫 OpenHands 依賴** | 主線 C 純 stdlib 免 venv;三 backend×雙引擎全綠 |
| [W6](PLAN_wave6.md) | Server tab、evict 正名、事件觸發 transcript、vendored Swagger、事件時間軸 | 可觀測性補強;內網零外部依賴(全 vendored) |
| [W7](PLAN_wave7.md) | profile goal/月預算、人類完成度評分、預算預檢、Agent Detail、概念/狀態機頁、REST /api/v1 | 把「員工績效」與對外 API 補齊 |

> W8 之後(dashboard 美化、W10 HIL 生命週期重設計、W11 互動服務、W12 專業化打包、
> W13 離線文件、docs/research 策展)的進展在 `HANDOFF.md` 與 `CHANGELOG.md`。

## 真 Jira 實測

- [TEST_real_jira.md](TEST_real_jira.md) — 對真 Jira Cloud 的端到端實測結果表(審批門鏈路、
  ADF 往返保真、email→accountId、fork claude 成本、冪等等),以及尚未實測的項目。

---

> 這些是**當時的計畫稿**,可能與最終實作有出入(例如 W10.2 HIL 行為在 W11 才落地、
> assignee 模型 W11 改恆定=Agent)。要看**現況**請以 `HANDOFF.md` + `docs/` 正式文件為準。
