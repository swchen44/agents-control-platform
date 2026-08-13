"""Workspace provisioning + health check(設計見 docs/design/workspace.md)。

一張票 → 一個隔離工作區。全新建立時依序:內容佈建(install 腳本 / copytree / 空)→
common skills 複製 → inject CLAUDE.md/AGENTS.md → 寫 TICKET.md。resume 時只刷新
TICKET.md,其餘跳過(native resume 綁 cwd,重跑會重貼/重 clone)。

Layout(以不變的 numeric issue_id 命名,native resume cwd-bound → 路徑永不變):
    <root>/tickets/<folder>/ws/          agent 工作目錄
                           /ws/TICKET.md  任務簡報(每輪刷新)
"""

from __future__ import annotations

import datetime
import os
import re
import shlex
import shutil
import subprocess
import time

from .logutil import get_logger
from .paths import common_hooks_dir, common_skills_dir, templates_dir
from .profiles import Profile
from .ticket import Ticket

log = get_logger("workspace")

_INJECT_FILE = "inject_claude_md_end.md"
_MARK_BEGIN = "<!-- BEGIN arcp inject -->"
_MARK_END = "<!-- END arcp inject -->"
_HUMAN_SIDECAR = ".arcp_human.md"     # Q10:人類指示累加(不被 TICKET.md 重渲染蓋掉)
RESUME_NOTE = ".arcp_resume_note"     # T10/T12 修:最新一則人類指示,下輪 resume
#                                       prompt 顯式帶上後即刪(native resume 的
#                                       agent 不一定重讀 TICKET.md,實測 haiku 不讀)
DESC_OVERRIDE = ".arcp_desc_override.md"   # M3:安全審人工修訂後的任務描述
#                                     (取代 TICKET.md 描述段;Jira description 不動)


def append_human_instruction(ws: str, text: str, now: float | None = None) -> None:
    """把人類在 HIL 表單輸入的補充指示,累加寫進 workspace 的 sidecar(data path)。
    下次 render_ticket_md 會讀進 TICKET.md 的「人類指示」段。單一寫入者=harness。"""
    text = (text or "").strip()
    if not text:
        return
    now = time.time() if now is None else now
    stamp = datetime.datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M")
    with open(os.path.join(ws, _HUMAN_SIDECAR), "a", encoding="utf-8") as f:
        f.write(f"- [{stamp}] {text}\n")


def pop_resume_note(ws: str) -> str:
    """讀最新人類指示並清掉(單次消費;dispatcher 組 resume prompt 用)。"""
    p = os.path.join(ws or "", RESUME_NOTE)
    if not (ws and os.path.isfile(p)):
        return ""
    note = open(p, encoding="utf-8").read().strip()
    try:
        os.remove(p)
    except OSError:
        pass
    return note


def _read_human_notes(ws: str) -> str:
    p = os.path.join(ws, _HUMAN_SIDECAR)
    return open(p, encoding="utf-8").read().strip() if os.path.isfile(p) else ""


def _read_desc_override(ws: str) -> str:
    """M3:安全審人工修訂後的描述(有 → 取代 TICKET.md 描述段)。"""
    p = os.path.join(ws, DESC_OVERRIDE)
    return open(p, encoding="utf-8").read().strip() if os.path.isfile(p) else ""


def _render_acceptance(profile: Profile | None) -> str:
    """把 profile.verify 渲染成人看得懂的驗收標準(= grader 的確定性門檻)。"""
    if not profile or not profile.verify:
        return "(此 profile 無確定性驗收步驟;以描述交付為準)"
    lines: list[str] = []
    for s in profile.verify:
        for fname, expected in (s.files or {}).items():
            suffix = f"(內容含 '{expected}')" if expected else ""
            lines.append(f"- [{s.name}] 必須產出檔案:`{fname}`{suffix}")
        if s.cmd:
            lines.append(f"- [{s.name}] 指令需通過:`{' '.join(s.cmd)}`")
        js = getattr(s, "json", None)           # C1:JSON 形狀(getattr 防禦舊/部分物件)
        if js:
            req = js.get("require") or []
            suffix = f"(必含鍵:{', '.join(req)})" if req else ""
            lines.append(f"- [{s.name}] 必須產出 JSON:"
                         f"`{js.get('file')}`{suffix}")
    return "\n".join(lines) or "(此 profile 無確定性驗收步驟)"


_VAR_RE = re.compile(r"\{(crid|email|prompt|key)\}")


def ticket_vars(t: Ticket) -> dict:
    """{crid}/{email}/{prompt}/{key} 插值變數(P 波,2026-08-13 定案)。
    值來自 description 頂部 yaml 三鍵 + 票 key;缺的鍵不代入(占位符保留)。"""
    from .triggers import parse_ticket_meta  # lazy:避免 import 環
    v = {k: val for k, val in parse_ticket_meta(t.description).items() if val}
    if t.key:
        v["key"] = t.key
    return v


def interpolate(text: str, tvars: dict) -> str:
    """單 pass 代入 {crid} 等占位符;未知/缺值一律保留原樣(不炸、不遞歸)。
    範圍限文本類(TICKET.md 各段、CLAUDE.md/AGENTS.md);verify cmd 不做
    (值來自 description=任何人可寫,插進 shell 指令是注入面)。"""
    if not text or "{" not in text:
        return text
    return _VAR_RE.sub(lambda m: tvars.get(m.group(1), m.group(0)), text)


def render_ticket_md(t: Ticket, profile: Profile | None = None,
                     base_url: str | None = None, human_notes: str = "",
                     desc_override: str = "") -> str:
    """任務簡報(agent prompt 第一句叫它讀這個)。內容 = Jira 票 + profile + 人類指示。
    M2:不再含「最新留言」段——人類指示的正式通道是 HIL 表單(human sidecar),
    Jira 留言多為 harness 稽核輸出,餵回 agent 沒價值且是注入面。
    P 波:goal/描述/人類指示皆過 interpolate(profile goal 寫「分析 {crid}」
    → 渲染成實際 CR 號,同一 profile 泛用於不同票)。"""
    head = [f"# {t.key}: {t.summary}", "",
            f"- issue_id: {t.id}", f"- 狀態: {t.state}",
            f"- assignee: {t.assignee or '-'}",
            f"- labels: {', '.join(t.labels) or '-'}"]
    if base_url and t.key:
        head.append(f"- Jira: {base_url.rstrip('/')}/browse/{t.key}")
    parts = ["\n".join(head)]
    tv = ticket_vars(t)
    if profile and profile.goal:
        parts.append(f"## 目標\n\n{interpolate(profile.goal, tv)}")
    if desc_override:                  # M3:安全審人工修訂版取代(原文在 Jira)
        parts.append("## 描述(要做什麼;經人工安全審修訂,原文見 Jira)\n\n"
                     + interpolate(desc_override, tv))
    else:
        parts.append("## 描述(要做什麼)\n\n"
                     + interpolate(t.description or "(無)", tv))
    if human_notes:                    # Q10:人類在 HIL 表單補的指示(累加,最新在下)
        parts.append("## 人類指示(累加,請一併遵循)\n\n"
                     + interpolate(human_notes, tv))
    parts.append("## 驗收標準(通過才算 SUCCESS)\n\n" + _render_acceptance(profile))
    return "\n\n".join(parts) + "\n"


def _slug(s: str) -> str:
    return "".join(c if (c.isalnum() or c in "-.") else "-" for c in s)


def inject_base_context(ws: str, base_ws: str, base_key: str,
                        base_url: str | None = None) -> str:
    """W10.3 跨票 base:把來源票 <base_key> 的脈絡複製進新票 workspace 的 BASE_<key>/。

    複製 base 的 TICKET.md(任務簡報)+ 最後一個 attempt envelope(產出證據),並寫一份
    HANDOFF.md 指路;同時在人類指示 sidecar 加一行,讓 TICKET.md「人類指示」段永遠指向
    BASE_ 目錄(dispatcher 於子票首次佈建後呼叫一次)。回傳 BASE_ 目錄路徑。
    """
    dest = os.path.join(ws, f"BASE_{_slug(base_key)}")
    os.makedirs(dest, exist_ok=True)
    copied: list[str] = []
    src_ticket = os.path.join(base_ws, "TICKET.md")
    if os.path.isfile(src_ticket):
        shutil.copy(src_ticket, os.path.join(dest, "TICKET.md"))
        copied.append("TICKET.md")
    attempts = os.path.join(os.path.dirname(base_ws), "attempts")
    if os.path.isdir(attempts):
        envs = sorted(f for f in os.listdir(attempts)
                      if f.endswith(".envelope.json"))
        if envs:
            shutil.copy(os.path.join(attempts, envs[-1]),
                        os.path.join(dest, envs[-1]))
            copied.append(envs[-1])
    note = [f"# 來自 {base_key} 的交接(a2a handoff base)", ""]
    if base_url and base_key:
        note.append(f"- 來源票:{base_url.rstrip('/')}/browse/{base_key}")
    note.append(f"- 已複製到本目錄:{', '.join(copied) or '(無可複製產物)'}")
    note.append("- 這是**只讀脈絡**;請閱讀後在本 workspace 繼續新任務。")
    with open(os.path.join(dest, "HANDOFF.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(note) + "\n")
    append_human_instruction(
        ws, f"本票由 {base_key} 跨票交接(base)而來;來源脈絡在 ./BASE_{_slug(base_key)}/"
            f"(含其 TICKET.md 與產出),請先閱讀再繼續。")
    return dest


def _resolve_targets(ws: str, claude_rel: str, agents_rel: str,
                     create_default: bool) -> list[str]:
    """統一目標解析(skills 目錄 / md 檔共用,docs/design/workspace.md):
    兩者都不存在 → 建 .claude 側(create_default 時);只一個存在 → 那個;兩個都在:
    同檔(soft/hard link)→ 一個;不同檔 → 兩個。"""
    c = os.path.join(ws, claude_rel)
    a = os.path.join(ws, agents_rel)
    ce, ae = os.path.exists(c), os.path.exists(a)
    if ce and ae:
        try:
            if os.path.samefile(c, a):
                return [c]
        except OSError:
            pass
        return [c, a]
    if ce:
        return [c]
    if ae:
        return [a]
    return [c] if create_default else []


def _run_install(ws: str, template: str, install_cmd: str, timeout: float) -> None:
    """執行 profile 的安裝命令:<argv…> <ws絕對> <template絕對>;cwd=template。
    stdout/stderr → logger;rc!=0 擲例外(provisioning 失敗)。"""
    argv = shlex.split(install_cmd) + [os.path.abspath(ws), os.path.abspath(template)]
    log.info("[install] run: %s (cwd=%s)", " ".join(argv), template)
    try:
        proc = subprocess.run(argv, cwd=template, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"install 逾時({timeout}s):{install_cmd}") from e
    except OSError as e:
        raise RuntimeError(f"install 無法執行:{install_cmd}: {e}") from e
    for line in (proc.stdout or "").splitlines():
        log.info("[install] %s", line)
    for line in (proc.stderr or "").splitlines():
        log.warning("[install:err] %s", line)
    if proc.returncode != 0:
        raise RuntimeError(f"install 失敗 rc={proc.returncode}:{install_cmd}")


def _copy_bundle(ws: str, names: list[str], lib_dir: str,
                 claude_rel: str, agents_rel: str, what: str) -> None:
    """config/<lib>/<name>/ 整包 → <目標>/<name>/(profile 選子集)。skills/hooks 共用。
    目標解析:.claude/* 或 .agents/*(統一規則,見 _resolve_targets)。"""
    if not names:
        return
    targets = _resolve_targets(ws, claude_rel, agents_rel, create_default=True)
    for t in targets:
        os.makedirs(t, exist_ok=True)
    for name in names:
        src = os.path.join(lib_dir or ".", name)
        if not os.path.isdir(src):
            raise FileNotFoundError(f"common {what} 不存在: {src}")
        for t in targets:
            dst = os.path.join(t, name)
            if os.path.isdir(dst):
                shutil.rmtree(dst)
            shutil.copytree(src, dst)


def _apply_inject(ws: str) -> None:
    """config/templates/inject_claude_md_end.md → append 到 CLAUDE.md/AGENTS.md 尾。
    marker 包住(冪等,重跑不重貼);都不存在 → 建 CLAUDE.md。"""
    inj = os.path.join(templates_dir() or ".", _INJECT_FILE)
    if not os.path.isfile(inj):
        return
    content = open(inj, encoding="utf-8").read().rstrip("\n")
    block = f"\n\n{_MARK_BEGIN}\n{content}\n{_MARK_END}\n"
    for tgt in _resolve_targets(ws, "CLAUDE.md", "AGENTS.md", create_default=True):
        existing = open(tgt, encoding="utf-8").read() if os.path.exists(tgt) else ""
        if _MARK_BEGIN in existing:
            continue                                   # 已注入,冪等跳過
        with open(tgt, "a", encoding="utf-8") as f:
            f.write(block)


def provision(root: str, ticket: Ticket, profile: Profile,
              base_url: str | None = None) -> str:
    """建立(或刷新)票工作區,回傳 ws 路徑。全新建立才跑佈建;resume 只刷新 TICKET.md。"""
    base = os.path.join(root, profile.workspace_folder.format(
        agent=_slug(profile.name), key=_slug(ticket.key), issue_id=ticket.id))
    ws = os.path.join(base, "ws")

    # 佈建原子性(A2 冪等):`.arcp_provisioned` = commit marker,佈建全部成功才寫。
    # 完整判定 grandfather 既有 workspace(有 TICKET.md 的舊 ws 視為完整,不誤刪);
    # ws 存在但「不完整」(install 中途 crash → 無 marker 也無 TICKET.md)→ 清掉重建,
    # 避免用半殘 workspace。resume(完整 ws)則整段跳過。
    marker = os.path.join(ws, ".arcp_provisioned")
    complete = os.path.isdir(ws) and (
        os.path.isfile(marker) or os.path.isfile(os.path.join(ws, "TICKET.md")))

    if not complete:
        if os.path.isdir(ws):
            shutil.rmtree(ws)                           # 清半殘,重建
        os.makedirs(base, exist_ok=True)
        tpl_root = templates_dir() or "."
        if profile.workspace_install:                   # 2a. install 腳本佈建
            os.makedirs(ws, exist_ok=True)
            tpl = (os.path.join(tpl_root, profile.workspace_template)
                   if profile.workspace_template != "empty" else tpl_root)
            timeout = float(profile.agent.get("timeout_sec", 300)) + 120
            _run_install(ws, tpl, profile.workspace_install, timeout)
        elif profile.workspace_template != "empty":     # 2b. copytree
            template = os.path.join(tpl_root, profile.workspace_template)
            shutil.copytree(template, ws)
        else:                                           # 2c. 空 ws
            os.makedirs(ws, exist_ok=True)
        for skill_path in profile.skills:               # 3a. 舊 file-based skills(相容)
            name = os.path.splitext(os.path.basename(skill_path))[0]
            dst = os.path.join(ws, ".claude", "skills", name)
            os.makedirs(dst, exist_ok=True)
            shutil.copy(skill_path, os.path.join(dst, "SKILL.md"))
        _copy_bundle(ws, profile.common_skills, common_skills_dir() or ".",
                     ".claude/skills", ".agents/skills", "skill")  # 3b. common skills
        _copy_bundle(ws, profile.common_hooks, common_hooks_dir() or ".",
                     ".claude/hooks", ".agents/hooks", "hook")     # 3c. hooks(Q8)
        if profile.inject_md:                           # 4. inject md
            _apply_inject(ws)
        # 4b. P 波:ws 根的 CLAUDE.md/AGENTS.md 代入 {crid} 等變數(一次性;
        # 代入後不再含占位符,重跑冪等)。skills 檔不動(共用資產,要用變數
        # 就在說明裡教 agent 讀 TICKET.md)。
        tv = ticket_vars(ticket)
        if tv:
            for fname in ("CLAUDE.md", "AGENTS.md"):
                p = os.path.join(ws, fname)
                if os.path.isfile(p):
                    txt = open(p, encoding="utf-8").read()
                    out = interpolate(txt, tv)
                    if out != txt:
                        with open(p, "w", encoding="utf-8") as f:
                            f.write(out)
        with open(marker, "w") as f:                    # ← commit:全部成功才立 marker
            f.write("ok\n")

    with open(os.path.join(ws, "TICKET.md"), "w") as f:  # 5. 任務簡報(每輪刷新)
        f.write(render_ticket_md(ticket, profile, base_url, _read_human_notes(ws),
                         _read_desc_override(ws)))
    return ws


def health_check(ws: str, ticket: Ticket, profile: Profile | None = None,
                 base_url: str | None = None) -> tuple[bool, str]:
    """(healthy, reason)。每次 resume 前跑(v5 §4.4)。TICKET.md 過期則刷新後仍健康。"""
    if not os.path.isdir(ws):
        return False, "workspace 目錄不存在"
    if not os.access(ws, os.W_OK):
        return False, "workspace 不可寫"
    ticket_md = os.path.join(ws, "TICKET.md")
    if not os.path.isfile(ticket_md):
        return False, "TICKET.md 遺失"
    fresh = render_ticket_md(ticket, profile, base_url, _read_human_notes(ws),
                         _read_desc_override(ws))
    if open(ticket_md).read() != fresh:                 # 票內容變了:刷新非損壞
        with open(ticket_md, "w") as f:
            f.write(fresh)
    return True, "ok"
