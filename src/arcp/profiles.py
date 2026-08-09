"""Inner-loop profiles (v5 §4.2): workspace / skills / agent / verify / loop.

Same fail-fast stance as routing.py: a bad profile dies at load, not at
dispatch. The `agent` block is deliberately opaque to everything except the
inner runner — swapping route B (openhands-acp) for route C (rawcli) touches
that block and the runner, nothing else (B-phase survival rule 2).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import yaml

from .paths import repo_root, templates_dir
from .routing import ConfigError

# W7(R3):未設 human_minutes_est 時的預設「人做同任務估時」(分鐘)。
# 使用者 2026-08-07:預設 4 小時,好讓效益一律算得出來。
DEFAULT_HUMAN_MINUTES_EST = 240.0


@dataclass
class VerifyStep:
    name: str
    files: dict[str, str | None] = field(default_factory=dict)
    cmd: list[str] | None = None


@dataclass
class Profile:
    name: str
    workspace_template: str          # empty | repo-checkout (later)
    workspace_folder: str            # e.g. tickets/{issue_id}
    skills: list[str]
    agent: dict                      # opaque to all but the inner runner
    verify: list[VerifyStep]
    max_attempts: int
    on_unknown: str                  # must be "pending" (v5 D3)
    max_budget_usd: float | None = None  # A4:超支→pending:budget(None=不限)
    require_approval: bool = False    # W2.3 起點審批門(per-profile)
    approver: str | None = None       # 審批者 email/accountId
    max_revisions: int = 3            # 退回重填上限
    retention_days: int = 270         # W3.3:終態後保留天數(0=不回收;DESIGN §3)
    human_minutes_est: float | None = None  # W3.5 C3:人做同任務估時(分),KPI 用
    # W7(R1):人可讀的 agent 目標,交人評分時寫進 description 的 agent:<profile> 段,
    # 讓人對照判斷完成度。None → 用 route/profile 名 fallback。
    goal: str | None = None
    # W7(R7):月預算上限(日曆月、跨票、per-profile;None=不限)。超過只能改此設定。
    max_budget_monthly_usd: float | None = None
    # workspace 佈建(docs/design/workspace.md):
    # workspace_install=安裝命令(argv;設了就用它佈建,不 copytree);
    # common_skills=從 config/skills/ 選的資料夾名;inject_md=是否注入 inject 檔
    workspace_install: str | None = None
    common_skills: list[str] = field(default_factory=list)
    common_hooks: list[str] = field(default_factory=list)  # Q8:config/hooks/ 選子集
    inject_md: bool = True
    # Q16:首次派工選 profile(A/B 測試 / 泛化 triage)。None=不選、直接用本 profile。
    # {candidates:[名], method:"random"|"script", script:"argv(method=script)"}
    select: dict | None = None
    # Q15:此 profile 來源 yaml 絕對路徑(inline=主檔;拆檔=config/profiles/<名>.yaml)。
    source_yaml: str = ""

    def est_minutes(self) -> float:
        """W7(R3):有效估時——未設回預設 240 分(4h),讓效益一律算得出來。"""
        return (self.human_minutes_est if self.human_minutes_est is not None
                else DEFAULT_HUMAN_MINUTES_EST)


def _collect_profile_raw(path: str) -> dict[str, tuple[dict, str]]:
    """Q15:主檔 inline profiles + config/profiles/<名>.yaml(檔名=名、內容=body)合併。
    回 {name: (body, source_yaml)};同名跨檔衝突 → fail-fast。"""
    with open(path) as f:
        doc = yaml.safe_load(f) or {}
    raw: dict[str, tuple[dict, str]] = {}
    for name, p in ((doc.get("inner_loop") or {}).get("profiles") or {}).items():
        raw[name] = (p or {}, os.path.abspath(path))
    pdir = os.path.join(os.path.dirname(os.path.abspath(path)), "profiles")
    if os.path.isdir(pdir):
        for fn in sorted(os.listdir(pdir)):
            if not fn.endswith((".yaml", ".yml")):
                continue                       # 略過 README.md 等
            name = os.path.splitext(fn)[0]
            fp = os.path.join(pdir, fn)
            with open(fp) as f:
                body = yaml.safe_load(f) or {}
            if name in raw:
                raise ConfigError(
                    f"profile '{name}' 同時在 {raw[name][1]} 與 {fp} 定義(衝突)")
            raw[name] = (body, fp)
    return raw


def load_profiles(path: str) -> dict[str, Profile]:
    profiles: dict[str, Profile] = {}
    for name, (p, _source) in _collect_profile_raw(path).items():
        ws = p.get("workspace") or {}
        loop = p.get("loop") or {}
        if loop.get("on_unknown", "pending") != "pending":
            raise ConfigError(
                f"profile {name}: on_unknown 只能是 pending(v5 D3——"
                f"UNKNOWN 不可自動重試,只有人能解除)")
        steps = []
        for v in p.get("verify") or []:
            step = VerifyStep(name=v.get("name", "verify"),
                              files=dict(v.get("files") or {}),
                              cmd=list(v["cmd"]) if v.get("cmd") else None)
            if not step.files and not step.cmd:
                raise ConfigError(f"profile {name}: verify '{step.name}' "
                                  f"需要 files 或 cmd 其中之一")
            steps.append(step)
        appr = p.get("approval") or {}
        agent = p.get("agent") or {}
        if not agent.get("backend"):
            raise ConfigError(f"profile {name}: agent.backend 必填")
        # W3.6(D1):isolation.provider 白名單 fail-fast(介面先行,不實驗)
        iso_provider = (agent.get("isolation") or {}).get("provider")
        if iso_provider is not None:
            from .isolation import PROVIDERS
            if iso_provider not in PROVIDERS:
                raise ConfigError(
                    f"profile {name}: isolation.provider 必須是 "
                    f"{list(PROVIDERS)}(拿到 {iso_provider!r})")
        venv = agent.get("venv")
        if venv:
            vpath = venv if os.path.isabs(venv) else os.path.join(
                repo_root() or ".", venv)   # venv 相對 repo root(同 inner_runner HERE)
            if not os.path.isdir(vpath):
                raise ConfigError(f"profile {name}: agent.venv 不存在: {vpath}")
        # template=class:非 "empty" 時視為 template folder path(相對 config/templates/),
        # fork 前整包複製成 workspace instance(docs/design/workspace.md)。fail-fast:
        # 不存在的 template 死在 load,不是 dispatch。
        ws_template = ws.get("template", "empty")
        if ws_template != "empty":
            tpath = os.path.join(templates_dir() or ".", ws_template)
            if not os.path.isdir(tpath):
                raise ConfigError(
                    f"profile {name}: workspace.template 資料夾不存在: {tpath}")
        profiles[name] = Profile(
            name=name,
            workspace_template=ws_template,
            # resume-safe 命名(§2):可讀前綴 + 不變 issue_id 尾綴。舊模板
            # 'tickets/{issue_id}' 仍相容(format 忽略多餘的 agent/key)。
            workspace_folder=ws.get("folder",
                                    "tickets/{agent}__{key}__{issue_id}"),
            skills=list(p.get("skills") or []),
            agent=agent,
            verify=steps,
            max_attempts=int(loop.get("max_attempts", 2)),
            on_unknown="pending",
            max_budget_usd=(float(loop["max_budget_usd"])
                            if loop.get("max_budget_usd") is not None else None),
            require_approval=bool(appr.get("required", False)),
            approver=appr.get("approver"),
            max_revisions=int(appr.get("max_revisions", 3)),
            retention_days=int(p.get("retention_days", 270)),
            human_minutes_est=(float(p["human_minutes_est"])
                               if p.get("human_minutes_est") is not None
                               else None),
            goal=(str(p["goal"]) if p.get("goal") is not None else None),
            max_budget_monthly_usd=(
                float(loop["max_budget_monthly_usd"])
                if loop.get("max_budget_monthly_usd") is not None else None),
            workspace_install=(str(ws["install"])
                               if ws.get("install") else None),
            common_skills=list(ws.get("common_skills") or []),
            common_hooks=list(ws.get("common_hooks") or []),
            inject_md=bool(ws.get("inject_md", True)),
            select=_parse_select(name, p.get("select")),
            source_yaml=_source)
    # Q16:候選存在性要等全部 profile 載完才驗(候選可能定義在後面)。
    for name, prof in profiles.items():
        if not prof.select:
            continue
        for cand in prof.select["candidates"]:
            if cand not in profiles:
                raise ConfigError(
                    f"profile {name}: select.candidates 的 '{cand}' 未定義")
    return profiles


def _parse_select(name: str, sel: dict | None) -> dict | None:
    """驗 select 區塊:candidates(prefix 須=本 profile 名)、method、script。"""
    if not sel:
        return None
    cands = list(sel.get("candidates") or [])
    if not cands:
        raise ConfigError(f"profile {name}: select 需要非空 candidates")
    for c in cands:
        if not c.startswith(name):
            raise ConfigError(
                f"profile {name}: select 候選 '{c}' 的 prefix 須為 '{name}'"
                f"(A/B 同族好管理)")
    method = sel.get("method", "random")
    if method not in ("random", "script"):
        raise ConfigError(
            f"profile {name}: select.method 須為 random|script(拿到 {method!r})")
    script = sel.get("script")
    if method == "script" and not script:
        raise ConfigError(f"profile {name}: select.method=script 需要 script 命令")
    return {"candidates": cands, "method": method,
            "script": str(script) if script else None}
