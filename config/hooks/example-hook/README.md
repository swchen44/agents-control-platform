# example-hook

common hook 的最小範例資料夾。profile 設 `workspace.common_hooks: [example-hook]`
時,整個 `config/hooks/example-hook/` 會被複製到 workspace 的 hooks 目錄
(`.claude/hooks/example-hook/` 或 `.agents/hooks/example-hook/`)。

把實際的 hook 腳本 + 掛載說明放這裡。
