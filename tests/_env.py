"""tests 路徑啟動 —— 把 repo/scripts 放進 sys.path,讓需要 import 可執行腳本
(detail_server / run_poller 等)的測試不論 cwd/所在資料夾都跑得動。

arcp 套件本身走 editable 安裝(`uv sync`),直接 import 即可,不需在此處理;
設定檔 / vendored 資產 / runner 由 `arcp.paths` 以 repo-root 相對解析。
需要這些的測試在頂端 `import _env` 即可(sys.path[0] 已是本檔所在的 tests/)。
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO, "scripts")

if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)
