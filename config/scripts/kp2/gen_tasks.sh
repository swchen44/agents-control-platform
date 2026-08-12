#!/bin/sh
# KP2 整測:agent-job 產任務清單(stdout JSON)。兩筆任務各帶不同 label
# 覆寫 → 驗證「script 回傳 labels 分流到不同 route」。config 的 labels
# (arcp.write)是保底;第二筆覆寫成 arcp.creview。
cat <<'EOF'
[
  {"summary": "[job] 寫短文:harness 自動化測試心得",
   "description": "prompt: 寫一篇關於 harness 自動化測試心得的短文\n\n主題:harness 自動化測試心得。"},
  {"summary": "[job] Review C code:strcpy 範例",
   "labels": ["arcp.creview"],
   "description": "請 review 以下 C 程式碼:\n\n```c\n#include <string.h>\nvoid save(char *in) {\n  char buf[8];\n  strcpy(buf, in);\n}\nint main(int argc, char **argv) {\n  if (argc > 1) save(argv[1]);\n  return 0;\n}\n```"}
]
EOF
