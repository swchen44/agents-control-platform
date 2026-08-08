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

from .paths import templates_dir
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

    def est_minutes(self) -> float:
        """W7(R3):有效估時——未設回預設 240 分(4h),讓效益一律算得出來。"""
        return (self.human_minutes_est if self.human_minutes_est is not None
                else DEFAULT_HUMAN_MINUTES_EST)


def load_profiles(path: str) -> dict[str, Profile]:
    with open(path) as f:
        doc = yaml.safe_load(f) or {}
    profiles: dict[str, Profile] = {}
    for name, p in ((doc.get("inner_loop") or {}).get("profiles") or {}).items():
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
        if venv and not os.path.isdir(venv):
            raise ConfigError(f"profile {name}: agent.venv 不存在: {venv}")
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
                if loop.get("max_budget_monthly_usd") is not None else None))
    return profiles
