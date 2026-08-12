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
    """宣告式設定的家:config*.yaml + templates/ + skills/(全 git 追蹤)。"""
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


def job_scripts_dir() -> str | None:
    """job(trigger)腳本的家:config/scripts/<subfolder>/xxx.sh(trigger.script 相對
    此;執行時 cwd 進 <subfolder>)。git 追蹤。(注意:與下方 scripts_dir=repo 的
    scripts/ runner 目錄不同。)"""
    return _under_root("config", "scripts")


def resolve_config_file(spec: str | None) -> str:
    """CLI --config 解析(與 ARCP_CONFIG 同規則):含路徑分隔/絕對 → 原樣;
    純檔名 → config/ 下;None → config_path()(env/預設)。"""
    if not spec:
        return config_path()
    if os.path.isabs(spec) or os.sep in spec \
            or (os.altsep and os.altsep in spec):
        return spec
    c = config_dir()
    return os.path.join(c, spec) if c else spec


def resolve_runtime(cli: str | None, cfg_runtime: str | None = None) -> str:
    """runtime(DB/events/workspaces)位置:CLI --runtime > config
    source.runtime_dir > repo/runtime。非絕對路徑相對 **repo root**(不綁
    cwd)——測試/正式各指一個 runtime,DB 完全分離(開發手冊「重跑整測」)。"""
    spec = cli or cfg_runtime
    if not spec:
        return runtime_dir() or "./runtime"
    if os.path.isabs(spec):
        return spec
    root = os.path.dirname(_under_root("runtime") or os.path.abspath("runtime"))
    return os.path.join(root, spec)


def resolve_config_script(argv) -> tuple[list, str]:
    """config 裡引用的腳本(trigger.script / profile.select.script)統一解析:
    argv[0] = 相對 `config/scripts/` 的路徑,**必須放 subfolder**(如 cq/scan.sh,
    不可直接放根)→ 回 (abs_argv, cwd=腳本所在 subfolder)。realpath 擋路徑穿越。
    無效 → ValueError(呼叫端 fail-safe);找不到 base → RuntimeError。"""
    base = job_scripts_dir()
    if not base:
        raise RuntimeError("找不到 config/scripts/(job_scripts_dir() 為 None)")
    real_base = os.path.realpath(base)
    full = os.path.realpath(os.path.join(base, argv[0]))
    if not full.startswith(real_base + os.sep):
        raise ValueError(f"script 路徑越界 config/scripts/:{argv[0]!r}")
    cwd = os.path.dirname(full)
    if os.path.realpath(cwd) == real_base:
        raise ValueError("script 必須放 config/scripts/ 的 subfolder"
                         f"(如 cq/scan.sh),不可直接放根:{argv[0]!r}")
    return [full, *argv[1:]], cwd


def common_skills_dir() -> str | None:
    """common skills 庫(config/skills/<name>/;profile.common_skills 選子集)。"""
    return _under_root("config", "skills")


def common_hooks_dir() -> str | None:
    """common hooks 庫(config/hooks/<name>/;profile.common_hooks 選子集)。"""
    return _under_root("config", "hooks")


def scripts_dir() -> str | None:
    """可執行腳本 + 被 spawn 的 runner(scripts/)。"""
    return _under_root("scripts")


def config_path() -> str:
    """解析設定檔路徑。ARCP_CONFIG:含路徑分隔或絕對 → 原樣用;純檔名 → 視為
    config/ 下的檔(讓 CI 的 `ARCP_CONFIG=config.example.yaml` 不綁 cwd)。
    未設 → config/config.yaml。全找不到 config 時退回 cwd 相對(舊行為)。"""
    env = os.environ.get("ARCP_CONFIG")
    c = config_dir()
    if env:
        if os.path.isabs(env) or os.sep in env or (os.altsep and os.altsep in env):
            return env
        return os.path.join(c, env) if c else env
    return os.path.join(c, "config.yaml") if c else "config.yaml"


def find_script(name: str) -> str:
    """定位一支被 spawn 的腳本(如 inner_rawcli_runner.py)於 scripts/;找不到仍回
    scripts/ 候選路徑供錯誤訊息可讀。"""
    s = scripts_dir()
    if s:
        p = os.path.join(s, name)
        if os.path.exists(p):
            return p
    return os.path.join(s, name) if s else name
