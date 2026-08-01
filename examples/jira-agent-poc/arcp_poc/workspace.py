"""Workspace + skills provisioning.

Given a Decision, create an isolated working folder for the issue and install
the skills the rule selected. Skills follow the AgentSkills convention
(a directory containing SKILL.md), which is what both Claude Code and the
OpenHands SDK load from a project's skill dirs.

For raw `claude -p`: Claude Code auto-discovers skills placed under the
project's `.claude/skills/<name>/` (and honors AGENTS.md/CLAUDE.md). We copy the
selected skill dirs there so a bare `claude -p` in that cwd sees them.
For OpenHands: the same dirs map to `.openhands/skills/` or are passed via the
start payload's agent_context.skills (report §4).
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass


@dataclass
class ProvisionResult:
    cwd: str
    installed_skills: list[str]


def provision(run_root: str, issue_key: str, skills: list[str],
              skills_source_dir: str,
              skill_target_subdir: str = ".claude/skills") -> ProvisionResult:
    """Create run_root/<issue_key>/ and copy selected skills into it.

    skills_source_dir: a directory containing <skill_name>/SKILL.md subdirs.
    """
    cwd = os.path.join(run_root, issue_key)
    os.makedirs(cwd, exist_ok=True)
    target_base = os.path.join(cwd, skill_target_subdir)
    os.makedirs(target_base, exist_ok=True)

    installed: list[str] = []
    for name in skills or []:
        src = os.path.join(skills_source_dir, name)
        if not os.path.isdir(src):
            # A real system would `git clone`/download here (or POST
            # /api/skills/install to an agent-server). We only handle local.
            continue
        dst = os.path.join(target_base, name)
        if os.path.exists(dst):
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        installed.append(name)
    return ProvisionResult(cwd=cwd, installed_skills=installed)
