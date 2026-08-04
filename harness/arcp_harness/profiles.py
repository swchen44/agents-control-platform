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

from .routing import ConfigError

_HARNESS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
        agent = p.get("agent") or {}
        if not agent.get("backend"):
            raise ConfigError(f"profile {name}: agent.backend 必填")
        venv = agent.get("venv")
        if venv and not os.path.isdir(venv):
            raise ConfigError(f"profile {name}: agent.venv 不存在: {venv}")
        # template=class:非 "empty" 時視為 template folder path(相對 harness 根),
        # fork 前整包複製成 workspace instance(DESIGN_lifecycle §1)。fail-fast:
        # 不存在的 template 死在 load,不是 dispatch。
        ws_template = ws.get("template", "empty")
        if ws_template != "empty":
            tpath = os.path.join(_HARNESS_ROOT, ws_template)
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
            on_unknown="pending")
    return profiles
