# B 期 Harness — Lessons Learned(逐步累積,勿刪)

> 開發過程踩到的坑與教訓,全數留存。條目按時間附加;每條都寫「症狀 → 根因 → 對策」。
> 其他 lessons 的存放位置索引見文末。

## 2026-08-03(Phase 0-1)

1. **python.org macOS Python 不帶系統 CA 憑證**
   症狀:urllib HTTPS 一律 `CERTIFICATE_VERIFY_FAILED`。
   根因:python.org 安裝版不用系統 keychain。
   對策:`jira_source._ssl_context()` 優先用 certifi 的 CA bundle,無 certifi
   再退預設 context(Linux/homebrew 不受影響)。

2. **新版 `/rest/api/3/search/jql` 對不存在的 project 回空集合、不報錯**
   症狀:`project = AGT` 查回 0 筆,誤判成「project 存在但沒票」。
   根因:新端點對無效 JQL 實體寬鬆處理(舊 /search 會報錯)。
   對策:**別用 search 驗證 project 存在**;用 `/project/search` 列舉或
   createmeta 端點確認。假陰性比報錯危險。

3. **Jira 改名不改 key**
   症狀:UI 上看到「AGT」用途的 project,API 找不到 AGT。
   根因:project 名稱 AgentLifetimeBoardv1 可改,**key 固定是建立時的 SCRUM**。
   對策:一切以 API 列舉的 key 為準;config 的 project key 開發前先驗證。
   (呼應 v5 C3:ticket key 也會因 move 改變——display 名稱一律不可當識別。)

4. **中文 locale 的 issue type 名稱**
   症狀:`issuetype: {name: "Task"}` 建票 400。
   根因:site 語系中文,issue type 叫「任務」(id 10003)。
   對策:名稱屬 locale 資料,不可 hardcode 英文;用 createmeta 查詢後帶入,
   長期應改用 **id**(呼應 v5 §6-19:customfield id 也一樣,查詢後快取)。

5. **urllib 的 HTTPError 預設吞掉 response body**
   症狀:只看到 `HTTP Error 400: Bad Request`,不知道 Jira 想說什麼。
   根因:urllib 不自動附 error body。
   對策:`_request` 捕 HTTPError 時把 `e.read()` 前 400 字接到 msg 再 raise。
   這一條救了第 3、4 條的除錯——**錯誤體浮出是 adapter 的必備品,不是加分項**。

9. **store 是 harness 唯一的記憶——wipe store + 票還開著 = 重派重跑**
   症狀:fault 測試用全域 JQL + 全新 store,M2 的舊票(SCRUM-2)被重新執行、
   多花一次錢、多一則重複 comment。
   根因:冪等靠 store;store 沒了,open 票在 poller 眼中就是新工作。
   對策:測試腳本一律 scope 自己的 JQL(label 過濾);**正式營運絕不 wipe
   store**(v5 D9 的備份要求由此更硬);長期解 = Agent Status 欄位落在 Jira
   側作第二記憶(v5 C2)。

10. **feedback 的資訊量決定 retry 成敗(evidence_only 的邊界)**
    症狀:attempt 2 聽話建了 extra.txt 但內容空,驗證仍敗——
    missing-file 的失敗證據不含 expected content,agent 無從得知。
    根因:FileChecklistGrader 對「缺檔」只報 missing,對「內容錯」才報
    expected/got。
    對策:驗證設計要與 feedback 資訊量匹配:existence-only 用 missing 即可;
    內容敏感的驗證要嘛讓任務描述含內容、要嘛給多一輪(content-mismatch
    feedback 會揭示 expected)。

## Session 作業教訓(跨專案通用)

6. **背景工作的 cwd 會漂移**:background shell 從「當下」的工作目錄啟動,
   而前景的 cd / 系統 reset 會改變它 → 背景指令一律帶絕對路徑或顯式 `cd &&`。
   (本專案已踩三次:selftest、compare_run、loop demo。)

7. **`| tail -N` 會吃掉批次輸出**:四路對照的前三路結果被 tail 吃掉,幸而
   逐跑落盤才救回 → **結果一律落檔,stdout 只當即時觀察**;harness 的
   journal/results.json 設計正是這條的制度化。

8. **pipeline 的 exit code 是最後一個指令的**:`python3 x.py | tail -1` 令
   `&&` 鏈失去防護 → 驗證步驟不要接 pipe,或用 `set -o pipefail`。

## 其他 lessons 的存放位置(索引)

- **A 路實測陷阱**(SIGTERM rc=0 假完成、killpg、事件粒度不可靠、workspace
  搬家、npx 預熱、quota 共用):research v3 §9.3 各項 + `examples/*/README.md`
- **筆電睡眠凍結計時器**:session memory `live-experiment-sleep-hazard` + v3 §9.3-1
- **OpenHands/ACP 陷阱**(litellm rust-wheel、90s startup timeout、批次無
  kill 窗口):`examples/openhands-acp-poc/PLAN.md` 陷阱實錄 + `COMPARISON.md`
- **v5 設計側陷阱清單**(22 條):`research/2026-08-jira-agent-harness-design-v5.md` §6
