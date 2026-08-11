#!/usr/bin/env bash
# 範例 agent-job 腳本:掃出「要處理的事」→ 印出 JSON 任務清單到 stdout。
# ARCP 會把每筆當成一張 Jira 票(像人填票)開出來 → 走 route/triage。
# 真實版可在此查 ClearQuest / DB / API,每筆帶上 crid;此範例只印固定示範。
set -euo pipefail
cat <<'JSON'
[
  {"summary": "[demo] 範例任務 A", "description": "這是 agent-job 開的示範票 A。",
   "labels": ["arcp.cr"], "crid": "WCNCR0000001"},
  {"summary": "[demo] 範例任務 B", "description": "示範票 B(無 crid)。", "labels": ["arcp.cr"]}
]
JSON
