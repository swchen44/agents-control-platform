> **內網替代(B 案,2026-08-13 已實測)**:公司內無 Claude in Chrome →
> 改用 **agent+browser skill** 驗:`config/skills/browser-verify/`(教 agent
> 用 `agent-browser` CLI)+ profile `kp2-browser`(allowed_tools 放行
> `Bash(agent-browser:*)`)。開一張 `arcp.browser` 票、description 寫
> checklist → agent 自己開頁/截圖/產 REPORT.md;自動化版=`it_kp2.py T15`
> (真票 4/4)。依賴:`npm i -g agent-browser && agent-browser install`
> (內網預帶 node+Chromium)。本檔的人工 checklist 仍適用於有 Claude in
> Chrome 的開發機。

# KP2 browser E2E checklist(看畫面驗 REST 驗不到的)

> 搭配 `tests/it_kp2.py`(REST integration 主力)使用:REST 驗資料正確,
> **這份驗人眼看到的**——看板欄位移動、comment 渲染、@mention、表單頁畫面。
> 執行者:已登入 Jira 的瀏覽器(人或 agent browser skill)。
> 前置:poller + detail_server 在跑;it_kp2.py 至少跑過一輪(有票可看)。

## B1. 看板欄位反映生命週期(主題 N 狀態同步)

開 KP2 board / list(https://swchen44.atlassian.net/jira/software/c/projects/KP2/list):

- [ ] `[it] T1` 票:結束後在 **Closed / 完成** 欄(不是 Cancelled——雙 done 挑錯是
      主題 N 修掉的 bug,這裡是回歸點)。
- [ ] `[job]` creview 票(被 T3 cancel):在 **Cancelled** 欄。
- [ ] `[it] T4` 審批票:cancel 前曾在 **Pending** 欄(歷程可從票的 History 看)。
- [ ] 進行中的票在 **In Progress** 欄;等評分的票在 **Resolve** 欄。

## B2. 票內容渲染(REST 只能驗文字,渲染要看)

打開任一張跑完的票:

- [ ] description 頂部 yaml 契約(`email: …`)顯示正常、**ARCP 區塊**
      (owner=control/human 段)分段清楚、無 JSON 殘渣。
- [ ] 交付物 comment:標題/清單/連結**有格式**(ADF 渲染,不是原始碼)。
- [ ] `[agent]` comment 的 @mention 是**藍色可點**(accountId 解析成功),
      且被 mention 的人**真的收到通知**(鈴鐺/信)——mention 打錯語法時文字
      看得到但不通知,只有人眼+通知能驗。
- [ ] outcome comment 的失敗證據(verify 訊息)換行正常可讀。

## B3. 一次性表單頁(人面向 UI)

從票的 comment 點表單連結(/form/<token>):

- [ ] 評分表單:交付物駕駛艙(成果敘事/附件/連結)渲染正常;email 欄必填;
      提交後顯示「已提交」;**再開同連結顯示已失效**(一次性)。
- [ ] 指令台(command_console 連結):顯示目前狀態、動態指令選單、
      「目前負責人」現值預填(K6)、破壞性指令有二次確認框。
- [ ] (有安全掃描時)security_review 表單:命中表 + 原文 + 修訂文字框。

## B4. ARCP dashboard 對照

開 http://127.0.0.1:8788:

- [ ] `/timeline` 粗看:KP2 各票色帶與看板狀態一致(藍=In Progress、
      黃=Pending/Resolve 等人、✔/✘ 終點);點列側欄連結可開。
- [ ] ticket 頁駕駛艙:abort_reason、owner email、用量與 Jira 側一致。

## 記錄

執行日期/執行者/結果(逐項 ✅/❌ + 截圖路徑)附在 PR 或 BACKLOG 主題 N 註記。

### 2026-08-12 首輪(agent browser,搭配 it_kp2.py 首跑)

- B1 ✅:KP2-7 Closed(非 Cancelled——雙 done 回歸點過)、KP2-6 Cancelled、
  KP2-3/4/5 Resolved、KP2-8 曾 Pending;KP2-1(無 label)不被接管留 To Do。
- B2 ✅:description yaml/ARCP 區塊分段正常;outcome comment(SUCCESS/驗證
  結果清單)與指令台連結 comment 渲染正常。
- B2 @mention(2026-08-12 二次驗)✅ + **抓到並修 bug**:初驗 comment 顯示
  灰色死文字 `[~accountid:712020:…]`(Cloud ADF 用純 text node 包 wiki 語法
  → 不渲染、不通知)。修 `text_to_adf` 拆 `[~accountid:ID]` 為 ADF **mention
  node** 後,comment 正確顯示藍色 `@fox44`(觸發 Jira 通知)。此 bug 影響
  K 期所有 @mention(approver watcher / HIL 通知)——REST 驗不到,只有
  browser 看得出。
- B3 ✅:評分表單駕駛艙(票/HIL(End)/grader 對照/花費/attempts/email 必填)
  渲染正常;已提交表單顯示「✓ 已提交」+ 唯讀提交內容(一次性語意)。
- B4 ✅:/timeline 每票一列、✔/✘ 終點與看板狀態一致。
- 同輪 REST(it_kp2.py):T1–T4 全過(T1 首跑兩敗因=腳本舊斷言+測試選到
  verify-bug 歷史票,修正後 T2 重跑 4/4;產品側三個 bug 見 KP2-B commit)。
