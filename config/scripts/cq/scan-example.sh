#!/bin/sh
# CQ 掃描 agent-job **範例**(內網接真 ClearQuest 時以此為模板)。
# 兩層 CRID 去重設計:
#   1) 本 script 可用 REST 預濾(示範如下):GET /api/v1/tickets/<CRID>
#      → 200=已開過票(略過)、404=新(輸出)。省 harness 日誌雜訊。
#   2) harness 必擋層(fire_agent_job):開票前查 DB(session.clearquest_id
#      + watch description),script 忘了預濾也不會重開。
# 用法:triggers 加一筆 agent-job 指到本檔(cwd=本資料夾);ARCP_API 可覆寫
# (預設打正式 dashboard;整測環境傳 http://127.0.0.1:8798)。
ARCP_API="${ARCP_API:-http://127.0.0.1:8788}"

# ── 1. 撈 CQ 清單(內網:換成真 ClearQuest 查詢;此處固定樣本示範)──
#    每行:CRID<TAB>摘要
cat <<'EOF' > /tmp/cq_rows.$$
WCNCR0100001	範例 CR:模組 A 當機
WCNCR0100002	範例 CR:設定頁排版跑掉
EOF

# ── 2. REST 預濾 + 組 JSON 任務清單(crid 進每筆;harness 會再擋一層)──
printf '['
first=1
while IFS="$(printf '\t')" read -r crid summary; do
  [ -z "$crid" ] && continue
  code=$(curl -s -o /dev/null -w '%{http_code}' \
         "$ARCP_API/api/v1/tickets/$crid" 2>/dev/null || echo 000)
  [ "$code" = "200" ] && continue          # 已開過票 → 略過
  [ $first = 1 ] || printf ','
  first=0
  printf '{"summary": "[CQ] %s", "crid": "%s", "description": "crid: %s 來源 ClearQuest,請依 CR 內容處理。\\n\\n%s"}' \
         "$summary" "$crid" "$crid" "$summary"
done < /tmp/cq_rows.$$
printf ']\n'
rm -f /tmp/cq_rows.$$
