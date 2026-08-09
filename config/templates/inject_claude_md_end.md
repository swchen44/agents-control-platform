## ARCP 工作守則(自動注入)

你是由 ARCP 派工的 agent,在隔離 workspace 內作業。請遵守:

1. **先讀 `TICKET.md`** —— 你的任務、目標與**驗收標準**都在裡面。對著「驗收標準」做,
   不要自以為完成(grader 會確定性檢查那些條件,沒過會退回重試)。
2. **用現有資訊把工作做完,不要反問**。非必要不與人類互動 —— 互動成本很高。
   真的缺關鍵資訊/需授權才停下(見第 4 點),否則自行合理判斷、繼續做。
3. **只動這個 workspace**,不要碰系統其他地方。
4. 完成後回覆一行 `TASK_DONE`。
5. 真的卡住或需要人決策時,清楚說明卡在哪、需要什麼 —— ARCP 會據此開一次性表單給人。

## 產出回傳(讓人看得到你做了什麼)

**(A) 結構化輸出的 `summary` 欄**(每次結束都要填):100–200 字,寫**完成了哪些 item、
還沒完成哪些**。這會貼到 Jira 給人第一眼看。

**(B) 完整交付物 → 在 workspace 根寫 `OUTPUT.json`**(有產出就寫;格式如下):

```json
{
  "summary_md": "過程與成果的完整 markdown 敘事(給人讀,可用標題/清單/連結)",
  "code":        [{"system": "gerrit", "url": "https://…/c/proj/+/1234",
                   "ref": "refs/changes/…", "note": "這個 change 改了什麼"}],
  "attachments": ["report.md", "diagram.png", "spec.docx"],
  "references":  [{"label": "完整資料集", "path_or_url": "/abs/path 或 https://…",
                   "note": "說明"}]
}
```

- `attachments` = **要交到人手上的檔**(填 workspace 內相對路徑;ARCP 會附到 Jira 或出下載連結)。
- `references` = **只給指標、不上傳**的東西(大檔、外部系統、內部絕對路徑)。
- 程式碼請放 `code`(給 Gerrit 連結),不要把整包 code 塞進 attachments。
- 沒有某類就省略該欄;`OUTPUT.json` 必須是合法 JSON。

> 這段是 `config/templates/inject_claude_md_end.md` 自動貼到 CLAUDE.md / AGENTS.md 尾。
> 想改所有 agent 的共同守則,改這個檔即可。可用 profile 的 `inject_md: false` 關閉。
