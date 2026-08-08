#!/usr/bin/env python3
"""W1.1 — provision 單元測(免 token,確定)。

驗 template=class→workspace=instance 複製 + resume-safe 命名(DESIGN_lifecycle
§1/§2):
  P1 template folder path → ws 是 template 的整包複本(copytree)
  P2 命名 = agent__key__issue_id(可讀前綴 + 不變 issue_id 尾綴)
  P3 empty → 空建 + 注入 skill(向後相容)
  P4 template 不存在 → load 時 ConfigError(fail-fast)
  P5 已存在 ws → 不重新複製(instance 狀態保留)

Usage: <venv>/python test_provision.py
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arcp.profiles import Profile, load_profiles  # noqa: E402
from arcp.routing import ConfigError  # noqa: E402
from arcp.ticket import Ticket  # noqa: E402
from arcp.workspace import provision  # noqa: E402

ok = True


def check(name, cond):
    global ok
    ok = ok and cond
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


def _ticket():
    return Ticket(id=10042, key="PROJ-1", summary="做點事", state="To Do",
                  assignee=None, assignee_id=None, labels=[], description="desc")


def _profile(**kw):
    base = dict(name="myagent", workspace_template="empty",
                workspace_folder="tickets/{agent}__{key}__{issue_id}",
                skills=[], agent={"backend": "rawcli"}, verify=[],
                max_attempts=2, on_unknown="pending")
    base.update(kw)
    return Profile(**base)


# -- P1/P2/P5:template 複製 + 命名 + instance 保留 --------------------------- #
root = tempfile.mkdtemp()
template = tempfile.mkdtemp()
with open(os.path.join(template, "seed.txt"), "w") as f:
    f.write("hello")
os.makedirs(os.path.join(template, "sub"))
with open(os.path.join(template, "sub", "a.txt"), "w") as f:
    f.write("A")

prof = _profile(workspace_template=template)  # 絕對路徑:join(root, abs)==abs
ws = provision(root, _ticket(), prof)
check("P1 template seed 複製", os.path.isfile(os.path.join(ws, "seed.txt"))
      and open(os.path.join(ws, "seed.txt")).read() == "hello")
check("P1 template 子目錄複製",
      open(os.path.join(ws, "sub", "a.txt")).read() == "A")
check("P2 命名 agent__key__issue_id",
      ws.endswith(os.path.join("tickets", "myagent__PROJ-1__10042", "ws")))
check("P1 TICKET.md 一併寫入", os.path.isfile(os.path.join(ws, "TICKET.md")))

with open(os.path.join(ws, "instance_state.txt"), "w") as f:
    f.write("live")
ws_again = provision(root, _ticket(), prof)
check("P5 已存在 ws 不重複複製(instance 狀態保留)",
      ws_again == ws and os.path.isfile(os.path.join(ws, "instance_state.txt")))

# -- P3:empty 向後相容(空建 + 注入 skill) ---------------------------------- #
skill_dir = tempfile.mkdtemp()
skill = os.path.join(skill_dir, "myskill.md")
with open(skill, "w") as f:
    f.write("# skill body")
prof3 = _profile(workspace_template="empty", skills=[skill])
ws3 = provision(tempfile.mkdtemp(), _ticket(), prof3)
check("P3 empty 空建", os.path.isdir(ws3) and not os.path.isfile(
    os.path.join(ws3, "seed.txt")))
check("P3 skill 注入", os.path.isfile(
    os.path.join(ws3, ".claude", "skills", "myskill", "SKILL.md")))

# -- P4:template 不存在 → load 時 ConfigError ------------------------------ #
bad_yaml = os.path.join(tempfile.mkdtemp(), "bad.yaml")
with open(bad_yaml, "w") as f:
    f.write("inner_loop:\n"
            "  profiles:\n"
            "    p:\n"
            "      workspace:\n"
            "        template: /nonexistent/xyz-does-not-exist\n"
            "      agent:\n"
            "        backend: rawcli\n")
try:
    load_profiles(bad_yaml)
    check("P4 template 不存在→ConfigError", False)
except ConfigError:
    check("P4 template 不存在→ConfigError", True)

print("test-provision:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
