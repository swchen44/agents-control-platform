#!/bin/sh
# 範例 install 腳本(docs/design/workspace.md)。ARCP 呼叫方式:
#   ./install.sh <workspace 絕對路徑> <template 絕對路徑>
# cwd = 本模板夾;stdout/stderr 會被 ARCP 用 logger 吐出;rc==0 才算成功。
set -e
WS="$1"          # 目標 workspace(agent 的工作目錄)
TPL="$2"         # 本模板夾絕對路徑

echo "[example install] 佈建 workspace: $WS (template=$TPL)"

# 1) 複製模板內的靜態內容(這裡示範:把 CLAUDE.md 帶進去)
[ -f "$TPL/CLAUDE.md" ] && cp "$TPL/CLAUDE.md" "$WS/CLAUDE.md"

# 2) 真實情境可在此 git clone / 產生設定 / 改檔,例如:
#    git clone --depth 1 https://intranet.example/repo.git "$WS/repo"
#    sed -i '' "s/__TOKEN__/xxx/" "$WS/repo/config.ini"

# 3) 放一個起手檔證明佈建跑過
echo "provisioned by example_template/install.sh" > "$WS/PROVISIONED.md"

echo "[example install] done"
exit 0
