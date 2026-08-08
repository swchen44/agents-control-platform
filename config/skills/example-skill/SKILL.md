---
name: example-skill
description: 最小範例 skill — 示範 config/skills/ 的資料夾結構,可照抄
---

# example-skill

這是 common skill 的最小範例。profile 設 `workspace.common_skills: [example-skill]`
時,整個 `config/skills/example-skill/` 會被複製到 workspace 的 skills 目錄
(`.claude/skills/example-skill/` 或 `.agents/skills/example-skill/`)。

把真正的技能指示寫在這裡(何時用、怎麼用、步驟、注意事項)。輔助檔(腳本、範本)
放同資料夾一併帶入。
