# SKILL: C code review(KP2 整測)

Review TICKET.md「描述」段裡的 C 程式碼,結果存 workspace 的 `REVIEW.md`:

- 結構:`# Review` 標題 + 三節:`## Bugs`(邏輯/記憶體錯誤)、
  `## Security`(溢位/未驗證輸入等)、`## Style`(命名/可讀性)。
- 每個發現一行:`- [嚴重度] 行號/片段:問題與修法`。沒有發現寫 `-(無)`。
- 描述裡沒有 C code 就寫明「未提供程式碼」並回 TASK_DONE。
- 不要修改或建立其他檔案;寫完回覆 TASK_DONE。
