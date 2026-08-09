# config/hooks/ — common hooks 庫

可重用的 hook 包,機制與 [`config/skills/`](../skills/README.md) **完全一致**:profile 用
`workspace.common_hooks: [name1, name2]` **選子集**,佈建時把選到的 `config/hooks/<name>/`
**整包**複製到 workspace 的 hooks 目錄。

目標目錄解析(見 [docs/design/workspace.md](../../docs/design/workspace.md) 統一規則):workspace
已有 `.claude/hooks` 或 `.agents/hooks` 就複製進去(兩個都在→都放;互為 link→放一次);
都沒有就建 `.claude/hooks` 再放。

每個 hook 一個資料夾。`example-hook/` 是可照抄的最小範例。hook 的實際觸發/設定依你用的
agent CLI 慣例(如 Claude Code 於 `.claude/settings.json` 掛;ARCP 只負責把檔佈建進去)。
