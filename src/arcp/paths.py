"""Repo-root 相對的路徑解析 —— 讓套件與腳本不論 cwd/所在資料夾都能定位設定
(config/)、vendored 資產(vendor/)、runtime 資料(runtime/)、workspace 模板與
common skills(config/templates、config/skills)、以及被 spawn 的 runner(scripts/)。

背景:W12 專業化把套件搬到 src/arcp/、可執行腳本搬到 scripts/;之後(harness→config
重構)設定進 config/、vendored 進 vendor/、runtime 資料進 runtime/。任何
`dirname(__file__)` 式的相對假設都會在搬移後失效(W12.1 就這樣讓 inner_runner /
workspace 找不到東西)。本模組以「向上找到含 pyproject.toml 的 repo root」為錨,一律
repo-root 相對解析,搬檔不再破壞。
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


def _under_root(*parts: str) -> str | None:
    r = repo_root()
    return os.path.join(r, *parts) if r else None


def config_dir() -> str | None:
    """宣告式設定的家:routes*.yaml + templates/ + skills/(全 git 追蹤)。"""
    return _under_root("config")


def vendor_dir() -> str | None:
    """離線 vendored 資產(cclog transcript renderer + swagger-ui + vis-timeline …)。"""
    return _under_root("vendor")


def runtime_dir() -> str | None:
    """運行狀態(harness.db + events.jsonl + runs/ + workspaces;gitignore)。"""
    return _under_root("runtime")


def templates_dir() -> str | None:
    """workspace 模板(config/templates/<name>_template + inject_claude_md_end.md)。"""
    return _under_root("config", "templates")


def common_skills_dir() -> str | None:
    """common skills 庫(config/skills/<name>/;profile.common_skills 選子集)。"""
    return _under_root("config", "skills")


def scripts_dir() -> str | None:
    """可執行腳本 + 被 spawn 的 runner(scripts/)。"""
    return _under_root("scripts")


def config_path() -> str:
    """解析設定檔路徑。ARCP_CONFIG:含路徑分隔或絕對 → 原樣用;純檔名 → 視為
    config/ 下的檔(讓 CI 的 `ARCP_CONFIG=routes.example.yaml` 不綁 cwd)。
    未設 → config/routes.yaml。全找不到 config 時退回 cwd 相對(舊行為)。"""
    env = os.environ.get("ARCP_CONFIG")
    c = config_dir()
    if env:
        if os.path.isabs(env) or os.sep in env or (os.altsep and os.altsep in env):
            return env
        return os.path.join(c, env) if c else env
    return os.path.join(c, "routes.yaml") if c else "routes.yaml"


def find_script(name: str) -> str:
    """定位一支被 spawn 的腳本(如 inner_rawcli_runner.py)於 scripts/;找不到仍回
    scripts/ 候選路徑供錯誤訊息可讀。"""
    s = scripts_dir()
    if s:
        p = os.path.join(s, name)
        if os.path.exists(p):
            return p
    return os.path.join(s, name) if s else name
