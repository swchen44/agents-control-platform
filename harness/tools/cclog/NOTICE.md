# NOTICE — vendored claude-code-log

- **上游**:https://github.com/daaain/claude-code-log
- **作者**:Daniel Demmel(dain@danieldemmel.me)
- **授權**:MIT(全文見 `LICENSE.upstream`)
- **版本**:v1.5.0,commit `0a3327d`(vendor 於 2026-08-06,自本機 clone
  `~/git/claude-code-log`)
- **vendor 範圍**:`claude_code_log/` Python 套件整包(內部耦合深——cache/dag/
  factories/providers 相互引用,拆片段風險高;TUI/cli 部分雖用不到但保留原樣,
  升級 = 重新整包覆蓋 + 重跑驗證)
- **上游碼修改**:無(zero-diff vendor)。我們的整合碼都在 wrapper
  `render_transcript.py`(ARCP 自寫,見檔頭),不混入上游套件。
- **依賴**:`.venv/`(不進 git)——click, dateparser, pydantic, jinja2, mistune,
  toml, textual, packaging, gitpython, pygments, quickjs-ng(照上游
  pyproject.toml);重建:
  `python3 -m venv .venv && .venv/bin/pip install click dateparser pydantic jinja2 mistune toml textual packaging gitpython pygments 'quickjs-ng>=0.15.1.1,<0.16'`

## 為什麼選它(W4/V0 除險結論,詳 ../../DESIGN_transcript.md)

Python 同棧可控、**原生渲染 sidechain/agentId(sub-agent)**、
`--provider codex` 支援 Codex(beta)。前案 cchv-server(Rust prebuilt)
的 `--export` 經 source 證實刻意丟棄 sidechain,故棄用。
