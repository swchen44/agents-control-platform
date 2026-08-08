#!/usr/bin/env python3
"""workspace 佈建新能力(docs/design/workspace.md):install 腳本、common skills 目標
解析、inject 冪等、TICKET.md 新段。免 token、確定性。"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import arcp.workspace as w  # noqa: E402
from arcp.ticket import Ticket  # noqa: E402

ok = fail = 0


def check(name: str, cond: bool) -> None:
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS  {name}")
    else:
        fail += 1; print(f"  FAIL  {name}")


def _tk(**kw) -> Ticket:
    base = dict(id=7, key="SCRUM-7", summary="做一件事", state="待辦",
                assignee=None, assignee_id=None, labels=["agent"],
                description="請建立 DONE.md", comments=[])
    base.update(kw)
    return Ticket(**base)


class _P:
    """最小 profile stub(只用到 workspace 會讀的欄位)。"""
    def __init__(self, **kw):
        self.name = kw.get("name", "p")
        self.workspace_folder = kw.get("workspace_folder", "tickets/{issue_id}")
        self.workspace_template = kw.get("workspace_template", "empty")
        self.workspace_install = kw.get("workspace_install")
        self.skills = kw.get("skills", [])
        self.common_skills = kw.get("common_skills", [])
        self.inject_md = kw.get("inject_md", True)
        self.goal = kw.get("goal")
        self.verify = kw.get("verify", [])
        self.agent = kw.get("agent", {"backend": "rawcli"})


# ── 目標解析(4 情境)──────────────────────────────────────────────── #
def _mkws() -> str:
    return tempfile.mkdtemp()

ws = _mkws()  # 兩者都不存在 → 建 .claude 側
t = w._resolve_targets(ws, ".claude/skills", ".agents/skills", create_default=True)
check("目標:都無 → .claude 側", t == [os.path.join(ws, ".claude/skills")])

ws = _mkws(); os.makedirs(os.path.join(ws, ".agents/skills"))
t = w._resolve_targets(ws, ".claude/skills", ".agents/skills", create_default=True)
check("目標:只 .agents 存在 → 用 .agents", t == [os.path.join(ws, ".agents/skills")])

ws = _mkws()
os.makedirs(os.path.join(ws, ".claude/skills")); os.makedirs(os.path.join(ws, ".agents/skills"))
t = w._resolve_targets(ws, ".claude/skills", ".agents/skills", create_default=True)
check("目標:兩個不同檔 → 兩個都放", len(t) == 2)

ws = _mkws()
os.makedirs(os.path.join(ws, ".claude/skills")); os.makedirs(os.path.join(ws, ".agents"))
os.symlink(os.path.join(ws, ".claude/skills"), os.path.join(ws, ".agents/skills"))
t = w._resolve_targets(ws, ".claude/skills", ".agents/skills", create_default=True)
check("目標:symlink 同檔 → 只一個", len(t) == 1)

# ── common skills 複製(monkeypatch common_skills_dir)──────────────── #
skroot = tempfile.mkdtemp()
os.makedirs(os.path.join(skroot, "aflow")); os.makedirs(os.path.join(skroot, "bflow"))
open(os.path.join(skroot, "aflow", "SKILL.md"), "w").write("a")
open(os.path.join(skroot, "bflow", "SKILL.md"), "w").write("b")
w.common_skills_dir = lambda: skroot          # patch module-level name
ws = _mkws(); os.makedirs(os.path.join(ws, ".agents/skills"))  # 已有 .agents → 進去
w._copy_common_skills(ws, ["aflow"])
check("common skill:選子集複製到既有 .agents/skills",
      os.path.isfile(os.path.join(ws, ".agents/skills/aflow/SKILL.md"))
      and not os.path.isdir(os.path.join(ws, ".agents/skills/bflow")))

# ── inject 冪等 + symlink 只貼一次(monkeypatch templates_dir)──────── #
tpl = tempfile.mkdtemp()
open(os.path.join(tpl, "inject_claude_md_end.md"), "w").write("守則:先讀 TICKET.md")
w.templates_dir = lambda: tpl
ws = _mkws(); open(os.path.join(ws, "CLAUDE.md"), "w").write("# base\n")
w._apply_inject(ws); w._apply_inject(ws)      # 跑兩次
body = open(os.path.join(ws, "CLAUDE.md")).read()
check("inject:冪等(marker 只一組)", body.count(w._MARK_BEGIN) == 1
      and "守則:先讀 TICKET.md" in body)
ws = _mkws()                                   # 都不存在 → 建 CLAUDE.md
w._apply_inject(ws)
check("inject:都無 → 建 CLAUDE.md", os.path.isfile(os.path.join(ws, "CLAUDE.md")))

# ── install 腳本(rc0 佈建 + rc!=0 失敗)────────────────────────────── #
tpl2 = tempfile.mkdtemp()
sh = os.path.join(tpl2, "install.sh")
open(sh, "w").write('#!/bin/sh\necho "ws=$1 tpl=$2"\ntouch "$1/INSTALLED"\nexit 0\n')
os.chmod(sh, 0o755)
ws = _mkws()
w._run_install(ws, tpl2, "./install.sh", timeout=30)
check("install:rc0 佈建成功(收到 ws 參數)", os.path.isfile(os.path.join(ws, "INSTALLED")))
bad = os.path.join(tpl2, "bad.sh"); open(bad, "w").write("#!/bin/sh\nexit 3\n"); os.chmod(bad, 0o755)
try:
    w._run_install(_mkws(), tpl2, "./bad.sh", timeout=30); _raised = False
except RuntimeError:
    _raised = True
check("install:rc!=0 → RuntimeError", _raised)

# ── TICKET.md 新段(goal / 驗收 / Jira 連結)───────────────────────── #
class _V:
    def __init__(self): self.name = "task-done"; self.files = {"DONE.md": None}; self.cmd = None

md = w.render_ticket_md(_tk(), _P(goal="把票做完並驗證", verify=[_V()]),
                        base_url="https://x.atlassian.net")
check("TICKET.md:含目標", "## 目標" in md and "把票做完並驗證" in md)
check("TICKET.md:含驗收標準(由 verify 渲染)",
      "驗收標準" in md and "DONE.md" in md)
check("TICKET.md:含 Jira 連結", "https://x.atlassian.net/browse/SCRUM-7" in md)

# ── 佈建原子性(A2:半殘 ws 重建 / 完整 ws 保留)────────────────────── #
root = tempfile.mkdtemp()
prof = _P(workspace_template="empty", inject_md=False)
ws = w.provision(root, _tk(), prof)               # 首次:完整佈建
check("原子性:完整佈建有 .arcp_provisioned marker",
      os.path.isfile(os.path.join(ws, ".arcp_provisioned")))
open(os.path.join(ws, "instance_state.txt"), "w").write("half-done work")
ws2 = w.provision(root, _tk(), prof)              # resume:完整 → 不重建
check("原子性:完整 ws resume 保留 instance 狀態",
      ws2 == ws and os.path.isfile(os.path.join(ws, "instance_state.txt")))

# 模擬 install 中途 crash 的半殘 ws:目錄在、有殘檔、無 marker 無 TICKET.md
half = os.path.join(tempfile.mkdtemp(), "tickets", "7", "ws")
os.makedirs(half); open(os.path.join(half, "PARTIAL_junk"), "w").write("x")
root2 = os.path.dirname(os.path.dirname(os.path.dirname(half)))
prof2 = _P(workspace_folder="tickets/{issue_id}", workspace_template="empty",
           inject_md=False)
ws3 = w.provision(root2, _tk(), prof2)            # 應偵測不完整 → rmtree 重建
check("原子性:半殘 ws(無 marker/TICKET.md)被清掉重建",
      not os.path.isfile(os.path.join(ws3, "PARTIAL_junk"))
      and os.path.isfile(os.path.join(ws3, ".arcp_provisioned")))

print(f"test-workspace-provision: {'PASS' if fail == 0 else 'FAIL'} ({ok}/{ok+fail})")
sys.exit(1 if fail else 0)
