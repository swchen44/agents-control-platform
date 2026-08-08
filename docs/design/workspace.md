# Workspace 佈建(create agent workspace)

> agent 跑在**隔離的 workspace**、不直接碰 Jira。這份文件定義一張票如何被佈建成一個
> agent 工作區:放什麼、按什麼順序、resume 時怎麼不重跑,以及四種「投進工作區的東西」
> 各自的分工。實作在 `src/arcp/workspace.py`;profile 欄位見 [profiles](../project-overview.md)。

## 四樣東西的分工(先理解這個)

一個 workspace 裡有四類內容,**性質與生命週期不同**,別混:

| 東西 | 回答 | 性質 | 何時產生 | 來源 |
|---|---|---|---|---|
| **TICKET.md** | 「**要做什麼**」 | 動態、**每票** | 每次 provision 寫 + 過期自動刷新 | code 從 Jira 票渲染 |
| **CLAUDE.md / AGENTS.md**(+ `inject_claude_md_end.md`) | 「**怎麼做/行為守則**」 | 靜態、profile 層 | 建立時貼一次(冪等 marker) | 模板 + 全域 inject 檔 |
| **`.claude/skills` / `.agents/skills`** | 「**有什麼能力**」 | 靜態、profile 層 | 建立時 copy 一次 | `config/skills/`(profile 選子集) |
| **workspace 模板骨架** | 「**起手長什麼樣**」 | 靜態、profile 層 | 建立時 copytree / install 一次 | `config/templates/<name>_template` |

**關鍵區別**:除 TICKET.md 外,其餘三樣都**只在 workspace 全新建立時做一次**;resume 時
整段跳過(native resume 綁 cwd,工作區已在,重跑會重貼/重 clone)。**只有 TICKET.md
每輪都會核對、票內容變了就刷新**(因為任務描述/留言會更新)。

## 目錄佈局(在 `config/` 下)

```
config/
  templates/
    inject_claude_md_end.md          # 全域;貼到 CLAUDE.md/AGENTS.md 尾
    example_template/                # ← 可照抄範例:含 install.sh + CLAUDE.md
    <name>_template/                 # 每個 profile 的起手骨架(可含 install 腳本 + 內容)
  skills/
    example-skill/                   # ← 可照抄範例(SKILL.md)
    aflow/  bflow/  …                # common skills 庫(整包資料夾;profile 選子集)
```

> 範例可直接照抄:[`config/templates/example_template/`](../../config/templates/example_template/)
> (install 腳本)、[`config/skills/example-skill/`](../../config/skills/example-skill/)、
> [`config/templates/inject_claude_md_end.md`](../../config/templates/inject_claude_md_end.md)。
> `config/routes.example.yaml` 的 `default` profile 有註解版的 install/common_skills/inject_md 用法。

## 佈建流程(provision)

```
provision(ticket, profile):
  ws = runtime/tickets/<workspace_folder>/ws            # issue_id 命名,resume-safe
  若 ws 完整(有 .arcp_provisioned 或既有 TICKET.md)→ 只跳到 step 5(resume)  ⚠️ 冪等
  若 ws 不完整(install 中途 crash:無 marker 且無 TICKET.md)→ rmtree 重建   ⚠️ 原子性
  全新建立(依序;任一步失敗 → provisioning 失敗,該 attempt 記 infra error):

  1. mkdir ws
  2. 內容佈建(三擇一,install 優先於 template):
     a. workspace_install 有設 → 當「命令」跑(見下方契約)
     b. 否則 workspace_template != "empty" → atomic copytree(.tmp → rename)
     c. 否則 → 空 ws
  3. common skills:for name in profile.common_skills → copytree
        config/skills/<name>/ → <skills 目標>/<name>/      (目標解析見下)
  4. inject:profile.inject_md 為 true 且 config/templates/inject_claude_md_end.md 存在時,
        把該檔內容(marker 包住)append 到 <md 目標> 尾    (目標解析見下)
  4b. 寫 .arcp_provisioned(commit marker;到這裡佈建全部成功 → 標記完整)
  5. 寫 ws/TICKET.md(每輪刷新,見下)
  回傳 ws
```

### install 腳本契約(`workspace_install`)

profile 寫**一整條命令**(不只檔名),沿用專案既有 trigger script 機制(`shlex.split`
→ argv → subprocess):

- 例:`uv run install.py` / `uvx some-tool` / `npx foo` / `./install.sh` / `python install.py`
- ARCP 在其後**附兩個絕對路徑參數**:`<argv…> <ws 絕對路徑> <template 絕對路徑>`
- **cwd = 該 template 資料夾**(讓 `install.py` 這種裸檔名解析得到);逾時沿用 `profile.timeout`
- 腳本自負責:git clone / copy / 改檔 / 再 copy 進 ws
- **stdout/stderr → logger 吐出**(self-log,不落 transcript);**rc==0 成功、非 0 失敗**
- 設了 install 就**不**自動 copytree(腳本拿得到 template 路徑,自己決定要不要複製)

### 統一「目標解析」規則(skills 與 md 共用)

skills 複製與 md 注入,都要順應模板已建立的慣例(`.claude/*` 或 `.agents/*`;`CLAUDE.md`
或 `AGENTS.md`),規則一致,只差「都沒有時建不建」:

| 情況 | skills(`.claude/skills` vs `.agents/skills`) | md(`CLAUDE.md` vs `AGENTS.md`) |
|---|---|---|
| 兩者都不存在 | **建 `.claude/skills`** 再放 | **建 `CLAUDE.md`** 再貼 |
| 只有一個存在 | 放進存在的那個 | 貼進存在的那個 |
| 兩個都在、**同檔**(soft/hard link) | 放 **一次** | 貼 **一次** |
| 兩個都在、**不同檔** | **兩個都放** | **兩個都貼** |

- 「同檔」判定:`os.path.samefile`(涵蓋 soft link 與 hard link)。
- md 注入用 marker 包住注入區(同 `sections.py` 手法)→ 即使重跑也不重貼(冪等)。
- 注入可**關閉**:`profile.inject_md: false`。

## TICKET.md(agent 的任務簡報)

agent prompt(dispatcher `BASE_PROMPT`)第一句就是「請先閱讀工作目錄裡的 TICKET.md」,
所以它是任務進入工作區的唯一管道。內容(渲染自 Jira 票 + profile):

```markdown
# {key}: {summary}

- issue_id: {id}
- 狀態: {state}
- assignee: {assignee}
- labels: {labels}
- Jira: {base_url}/browse/{key}        ← 有真票 key + base_url 時;trigger 無票則略

## 目標
{profile.goal}                          ← profile 層的總目標(無則略)

## 描述(要做什麼)
{description}                            ← Jira 描述,人類寫的任務主體

## 驗收標準(通過才算 SUCCESS)
{由 profile.verify 渲染}                 ← 讓 agent 事先知道 grader 的確定性檢查門檻
  - [<step.name>] 必須存在檔案:`X`(內容含 '…')
  - [<step.name>] 指令需通過:`Y`

## 最新留言(最多 5 則)
- [author] {body 前 300 字}
```

**新增三段的理由**:
- **Jira 連結** —— 人從 workspace/transcript 反查回 Jira 票;也讓 agent 知道自己在辦哪張。
- **目標(goal)** —— profile 層的總目標,補足單票描述可能沒講的「為何/驗收精神」。
- **驗收標準** —— 從 `profile.verify`(檔案存在/內容/指令)渲染成人看得懂的門檻,讓 agent
  **對著證據做**(loop on evidence),而非自以為完成 → 直接呼應[證據型停止](../decisions.md)。

**刷新語意**:`health_check` 在每次 resume 前比對 TICKET.md;票內容變了(新留言/改描述)
就重渲染,視為**資訊更新非損壞**(仍回報健康)。這是四樣東西裡唯一會每輪更新的。

## 失敗與健康

- provisioning 任一步失敗(install rc≠0、copytree 爆、skills 來源缺)→ 該 attempt 記
  `workspace_unhealthy` / infra error,不假裝成功。
- `health_check`(resume 前必跑):ws 不存在/不可寫/`TICKET.md` 遺失 → 不健康 → 重新
  provision;TICKET.md 過期 → 刷新後仍健康。

## Profile 欄位

| 欄位 | 型別 | 說明 |
|---|---|---|
| `workspace_template` | str | 模板夾(相對 `config/templates/`),如 `myagent_template`;或 `empty` |
| `workspace_install` | str? | 安裝命令(argv);設了就用它佈建、不自動 copytree |
| `common_skills` | list[str] | 從 `config/skills/` 選的資料夾名;預設 `[]` |
| `inject_md` | bool | 是否注入 `inject_claude_md_end.md`;預設 `true`(檔不存在則自然跳過) |
| `goal` | str? | profile 層總目標;渲染進 TICKET.md「目標」段 |
| `verify` | list | 確定性驗收步驟(files/cmd);渲染進 TICKET.md「驗收標準」段,亦是 grader 的依據 |
| `skills`(舊,逐檔) | list | 保留相容;`common_skills`(資料夾)為之後主推 |

## 與其他文件的關係

- 生命週期(template=class → workspace=instance、resume-safe 命名)見
  [lifecycle](lifecycle.md);證據型停止見 [decisions D2](../decisions.md);
  三方描述分段(human/control/agent)見 [interaction](interaction.md)。
- ⚠️ 本文為**目標設計**;實作(`workspace.py` 的 install-runner / common-skills /
  inject / 目標解析,及 `config/` 佈局)隨 harness→config 重構一起落地。
