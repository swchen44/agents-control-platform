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
