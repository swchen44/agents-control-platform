## ARCP 工作守則(自動注入)

你是由 ARCP 派工的 agent,在隔離 workspace 內作業。請遵守:

1. **先讀 `TICKET.md`** —— 你的任務、目標與**驗收標準**都在裡面。對著「驗收標準」做,
   不要自以為完成(grader 會確定性檢查那些條件,沒過會退回重試)。
2. **用現有資訊把工作做完,不要反問**。非必要不與人類互動 —— 互動成本很高。
   真的缺關鍵資訊/需授權才停下(見第 4 點),否則自行合理判斷、繼續做。
3. **只動這個 workspace**,不要碰系統其他地方。
4. 完成後回覆一行 `TASK_DONE`。
5. 真的卡住或需要人決策時,清楚說明卡在哪、需要什麼 —— ARCP 會據此開一次性表單給人。

> 這段是 `config/templates/inject_claude_md_end.md` 自動貼到 CLAUDE.md / AGENTS.md 尾。
> 想改所有 agent 的共同守則,改這個檔即可。可用 profile 的 `inject_md: false` 關閉。
