# config/skills/ — common skills 庫

可重用的 skill 包。profile 用 `workspace.common_skills: [name1, name2]` **選子集**,佈建時
把選到的 `config/skills/<name>/` **整包**複製到 workspace 的 skills 目錄。

目標目錄解析(見 [docs/design/workspace.md](../../docs/design/workspace.md)):workspace 已有
`.claude/skills` 或 `.agents/skills` 就複製進去(兩個都在→都放;互為 link→放一次);都沒有
就建 `.claude/skills` 再放。

每個 skill 一個資料夾,內含 `SKILL.md`(Claude Code / Agent Skills 慣例)與任何輔助檔。
本資料夾的 `example-skill/` 是可照抄的最小範例。
