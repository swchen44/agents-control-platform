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

## vendor/ — 內含元件(W5.9,離線/內網用)

cclog 的互動時間軸原會從 unpkg CDN 動態載 vis-timeline。內網不可外連 →
把元件抓進 `vendor/`,`render_transcript.py` 把 HTML 內的 unpkg URL 改寫成
dashboard 本地路徑 `/tvendor/...`;dashboard 另對 transcript HTML 加 CSP
硬擋任何外部載入(雙保險)。

- `vendor/vis-timeline.min.js` / `.min.css`:**vis-timeline 8.5.3**
  (https://github.com/visjs/vis-timeline,MIT/Apache-2.0),
  vendor 於 2026-08-07,來源 `https://unpkg.com/vis-timeline/...`。
  升級:重新 curl 兩檔覆蓋即可(URL 見 render_transcript._CDN_REWRITES)。

## 為什麼選它(W4/V0 除險結論,詳 ../../DESIGN_transcript.md)

Python 同棧可控、**原生渲染 sidechain/agentId(sub-agent)**、
`--provider codex` 支援 Codex(beta)。前案 cchv-server(Rust prebuilt)
的 `--export` 經 source 證實刻意丟棄 sidechain,故棄用。
