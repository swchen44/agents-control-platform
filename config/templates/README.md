# config/templates/ — workspace 模板

profile 建立工作區時的**起手骨架**。機制與流程見 [docs/design/workspace.md](../../docs/design/workspace.md)。

## 內容

- **`inject_claude_md_end.md`** — 全域;佈建最後一步貼到 workspace 的 `CLAUDE.md` /
  `AGENTS.md` 尾(marker 包住、冪等)。共同行為守則放這。profile `inject_md: false` 可關。
- **`<name>_template/`** — 每個 profile 的模板夾。profile 用 `workspace.template: <name>_template`
  指定,佈建時**整包 copytree** 成新 workspace instance。裡面可放 `CLAUDE.md`/`AGENTS.md`、
  `.claude/skills/`、hooks、種子檔等。
- 需要**複雜佈建**(git clone / 改檔 / 條件複製)時,在模板夾放安裝腳本,profile 用
  `workspace.install: <命令>` 指定(見 `example_template/`)。設了 install 就用它、不 copytree。

## 慣例

- 模板夾名建議 `<profile 名>_template`(可讀、對得上)。
- install 命令支援 `uv run x.py` / `uvx x` / `npx x` / `./x.sh` / `python x.py`;ARCP 會在
  其後附兩個絕對路徑參數:`<workspace 路徑> <template 路徑>`,cwd = 模板夾,rc==0 才算成功。
