# A/B 實跑對照:raw supervisor vs OpenHands ACP(2026-08-03)

> 方法:**同一任務**(循序建檔 step1~5 內容鏈)、**同一判準**(路線 A 的
> `FileChecklistGrader`),同日同機實跑。數據源 `runtime_compare/results.json`
> (`compare_run.py` 產出;A 路另有 journal、B 路有 OpenHands 事件流可稽核)。
> 證據級別:標「實測」= 本輪實跑;標「歷史實測」= jira-agent-poc 先前矩陣;
> 標「分析」= 原始碼/文件推論(沿用 research v3 慣例)。

## 1. 量化結果(實測)

| | A-raw claude | B-OpenHands claude | A-raw codex | B-OpenHands codex |
|---|---|---|---|---|
| 結果 | ✅ done,grader PASS | ✅ done,grader PASS | ⛔ quota | ⛔ quota |
| 時長 | 30.4s | 31.6s | — | — |
| 事件數 | **248** | **14** | — | — |
| 事件粒度 | thinking delta 62、raw stream 162、tool 5+5 | ACPToolCallEvent 10、Message/Action/Observation | — | — |
| 成本 | $0.053(haiku,可控) | 未落地(冒煙 $0.45,adapter 預設模型) | — | — |
| session id | ✅ 預指定 | ✅(SDK 持久化) | — | — |

⛔ **codex 兩路皆被 ChatGPT 用量額度擋下**(`You've hit your usage limit... Aug 31st`)
——非相容性問題:A 路 codex 歷史實測 2×2 矩陣全過;B 路 codex-acp@1.1.2 **冒煙已
PASS**(68s、14 事件、file probe 過)。對照數據點待額度重置後補。
**營運發現(實測)**:訂閱 quota 是跨路線共用資源,兩條路都吃同一個 codex 帳號額度
→ pipeline 需要預算/節流管理。

## 2. 粒度判讀(實測)

同一任務,A 的原生流是 B 的 **~18 倍**事件量:

- **A(248 事件)**:含 token 級 thinking delta 與 stream_event——這是 watchdog
  「秒級 stall 偵測」與 token 計量的原料;代價是要自己 normalize(driver 層)。
- **B(14 事件)**:乾淨的語意層(工具呼叫/訊息/觀察),跨 agent 統一、不用自己寫
  driver;代價是**細粒度 watchdog 失去原料**——兩個工具呼叫之間若 agent 卡在
  thinking,B 路看不到心跳。

## 3. 質化對照

| 面向 | A raw | B OpenHands SDK(in-process) | 證據 |
|---|---|---|---|
| Setup | 零依賴,即開即用 | venv + SDK 重依賴 + npx adapter 預熱(首跑 3-4 分鐘,>SDK 90s timeout,會留半殘快取) | 實測 |
| Auth | CLI 本機登入 | **同樣吃本機登入,免 API key**(claude+codex 皆驗證) | 實測 |
| 模型控制 | `--model` 直接指定(haiku 省 8 倍) | `acp_model` 需額外設定,預設吃 adapter 預設 | 實測(A)/分析(B) |
| 終止語意 | 事件+exit code+grader(已釘陷阱) | Conversation.run() 返回 + ConversationErrorEvent(結構化錯誤,如 quota) | 實測 |
| Recovery | 三段梯度全實測(2×2×2 + workspace 搬家) | `session/load` + `acp_resume_session_id`(SDK 碼在,**未實跑**);transcript 渲染器未接線 | 歷史實測(A)/分析(B) |
| 錯誤面 | stderr 落 journal 自己判讀 | ConversationErrorEvent 帶 code/detail(quota 錯誤即由此精準捕獲) | 實測 |
| 依賴/版本面 | CLI schema 漂移(fixtures 回歸護欄) | SDK 1.39.1 + adapter pin(claude-agent-acp 已落後 npm 20 版)+ CLI 三層漂移 | 實測 |

## 4. 結論(維持 v3 §7 混合路線,新增實證)

1. **B 路線可行性已從「分析」升級為「實跑」**(claude 全綠;codex 冒煙綠、對照
   待 quota):SDK in-process headless 跑得動、auth 零設定、錯誤結構化。
2. **A 仍是一級公民的理由更具體了**:細粒度事件(watchdog 原料)、模型/成本可控、
   零依賴。B 的 14 事件語意層適合「要乾淨統一介面、不在乎秒級觀測」的場景。
3. **可插拔架構的正確性獲證**:同一 grader 判準跨 A/B 直接可用——差異化層
  (grader/recovery loop/escalation)確實獨立於 runtime 選擇。
4. 待補:B 路 resume 實跑(`acp_resume_session_id`)、codex 對照數據點(quota 重置後)、
   B 路成本落地(conversation_stats 持久化)。
