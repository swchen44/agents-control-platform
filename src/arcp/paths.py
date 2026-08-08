"""Repo-root 相對的路徑解析 —— 讓套件與 harness 腳本不論 cwd/所在資料夾都能定位
設定檔(harness/routes.yaml)、vendored 資產(harness/tools/)與被 spawn 的 runner
腳本(scripts/inner_*.py)。

背景:W12 專業化把套件搬到 src/arcp/、可執行腳本搬到 scripts/、設定與 vendored 資產
留在 harness/。任何 `dirname(__file__)` 式的相對假設都會在搬移後失效(W12.1 就這樣讓
inner_runner 找不到 runner)。本模組以「向上找到含 pyproject.toml 的 repo root」為錨,
一律 repo-root 相對解析,搬檔不再破壞。
"""
from __future__ import annotations

import os


def repo_root(start: str | None = None) -> str | None:
    """由 start(預設本檔)向上找含 pyproject.toml 的目錄;找不到回 None。"""
    d = os.path.dirname(os.path.abspath(start or __file__))
    while True:
        if os.path.exists(os.path.join(d, "pyproject.toml")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def harness_dir() -> str | None:
    """設定 + vendored 資產 + runtime 的工作區(repo/harness)。"""
    r = repo_root()
    return os.path.join(r, "harness") if r else None


def scripts_dir() -> str | None:
    """可執行腳本 + 被 spawn 的 runner(repo/scripts)。"""
    r = repo_root()
    return os.path.join(r, "scripts") if r else None


def config_path() -> str:
    """解析設定檔路徑。ARCP_CONFIG:含路徑分隔或絕對 → 原樣用;純檔名 → 視為
    harness/ 下的檔(讓 CI 的 `ARCP_CONFIG=routes.example.yaml` 不綁 cwd)。
    未設 → harness/routes.yaml。全找不到 harness 時退回 cwd 相對(舊行為)。"""
    env = os.environ.get("ARCP_CONFIG")
    h = harness_dir()
    if env:
        if os.path.isabs(env) or os.sep in env or (os.altsep and os.altsep in env):
            return env
        return os.path.join(h, env) if h else env
    return os.path.join(h, "routes.yaml") if h else "routes.yaml"


def find_script(name: str) -> str:
    """定位一支可執行/被 spawn 的腳本(如 inner_rawcli_runner.py)。先找 scripts/,
    退回 harness/(搬移過渡期並存),最後回 scripts/ 候選路徑供錯誤訊息可讀。"""
    for base in (scripts_dir(), harness_dir()):
        if base:
            p = os.path.join(base, name)
            if os.path.exists(p):
                return p
    s = scripts_dir()
    return os.path.join(s, name) if s else name
