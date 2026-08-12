"""profile 選擇(A/B 測試 / 泛化 triage,J4 遞歸)。

首次派工時,若 route 命中的 profile 有 `select`,決定實際要鎖定哪個 profile:
  method=random → 從 [main + candidates](**同族**、prefix=本名)隨機分流(A/B)。
  method=script → ticket/clearquest(含 crid)/候選/**all_profiles** 以 JSON 餵 stdin,
                  stdout 回一 profile 名。**軸 B**:可回**任何已定義 profile**(不限候選);
                  `notfound`→中止(ABORTED);未定義/壞輸出/rc≠0→fail-safe 回 current。
**遞歸(軸 A)**:選中的 profile 若自己也有 select 就再跑一層 → 多層 triage 樹。終止:
無 select(葉)/回自己/繞圈(走過)/fail-safe/第 10 層截斷。鎖定最後結果(resume 不重選)。

triage 與 A/B 共用此機制:選到 require_approval=true 的要人放行、false 的直接跑。
"""
from __future__ import annotations

import json
import random
import shlex
import subprocess

from .logutil import get_logger
from .paths import config_path
from .ticket import Ticket

log = get_logger("selection")

_SCRIPT_TIMEOUT = 60.0
# select 明確判不出適用 profile 的哨值 → dispatcher 中止(ABORTED),不派工。
UNTRIAGEABLE = "notfound"


def _pool(profile) -> list[str]:
    """[main] + candidates,去重保序。"""
    out = [profile.name]
    for c in (profile.select or {}).get("candidates", []):
        if c not in out:
            out.append(c)
    return out


def _script_input(ticket: Ticket, profile, pool: list[str],
                  yaml_of: dict, clearquest_id: str | None,
                  all_profiles: list[str]) -> dict:
    raw = getattr(ticket, "raw", {}) or {}
    created = (((raw.get("fields") or {}).get("created")) if raw else "") or ""
    return {
        "ticket": {"id": ticket.id, "key": ticket.key,
                   "summary": ticket.summary or "",
                   "description": ticket.description or "",
                   "created": created, "updated": getattr(ticket, "updated", ""),
                   "labels": list(ticket.labels or [])},
        "clearquest": {"crid": clearquest_id or "", "title": ""},
        "original": {"name": profile.name, "yaml": yaml_of.get(profile.name, "")},
        "candidates": [{"name": n, "yaml": yaml_of.get(n, "")} for n in pool],
        # J4 軸 B:腳本可回**任何**已定義 profile(不限 candidates)。
        "all_profiles": all_profiles,
    }


_MAX_SELECT_DEPTH = 10          # J4:遞歸最多 10 層(防繞圈/失控)


def _select_once(ticket: Ticket, profile, profiles: dict,
                 clearquest_id: str | None = None) -> tuple[str, dict]:
    """單層 select。回 (chosen_name, meta)。無 select → (main, {});失敗一律回 main
    (=current)。method=script(J4 軸 B)可回**任何已定義 profile**;random 仍限候選池。"""
    cfg = getattr(profile, "select", None)
    if not cfg:
        return profile.name, {}
    pool = [n for n in _pool(profile) if n in profiles]
    method = cfg.get("method", "random")
    meta = {"method": method, "pool": pool, "original": profile.name}

    if method == "random":
        chosen = random.choice(pool)       # 非密碼用途(A/B 分流;限同族候選)
        meta["chosen"] = chosen
        return chosen, meta

    # method == "script":JSON stdin → stdout 回 profile 名(可為任何已定義 profile)
    yaml_of = {n: (getattr(profiles[n], "source_yaml", "") or config_path())
               for n in pool}
    all_names = sorted(profiles.keys())
    argv = shlex.split(cfg["script"])
    try:                     # M1:必在 config/scripts/<subfolder>/、cwd 切 subfolder
        from .paths import resolve_config_script
        abs_argv, cwd = resolve_config_script(argv)
    except (ValueError, RuntimeError) as e:
        log.warning("select script 路徑無效 ticket=%s: %s → fallback %s",
                    ticket.key, e, profile.name)
        meta.update(chosen=profile.name, error=str(e))
        return profile.name, meta
    try:
        proc = subprocess.run(
            abs_argv, cwd=cwd,
            input=json.dumps(_script_input(ticket, profile, pool,
                                           yaml_of, clearquest_id,
                                           all_names)),
            capture_output=True, text=True, timeout=_SCRIPT_TIMEOUT)
    except (subprocess.TimeoutExpired, OSError) as e:
        log.warning("select script 失敗 ticket=%s: %s → fallback %s",
                    ticket.key, e, profile.name)
        meta.update(chosen=profile.name, error=str(e))
        return profile.name, meta
    for line in (proc.stderr or "").splitlines():
        log.info("[select:%s] %s", ticket.key, line)
    if proc.returncode != 0:                    # 腳本錯 → fail-safe 回 current
        meta.update(chosen=profile.name, error=f"rc={proc.returncode}")
        log.warning("select script rc=%s → fallback %s",
                    proc.returncode, profile.name)
        return profile.name, meta
    # 嚴格 JSON stdout:{"profile": "<profile 名|notfound>", "reason": "..."}
    try:
        out = json.loads((proc.stdout or "").strip())
        chosen = str(out["profile"]).strip()
    except (ValueError, KeyError, TypeError) as e:
        meta.update(chosen=profile.name, error=f"bad stdout: {e}")
        log.warning("select script stdout 非合法 JSON → fallback %s(%s)",
                    profile.name, e)
        return profile.name, meta
    reason = str(out.get("reason") or "").strip()
    if chosen == UNTRIAGEABLE:                   # 明確判不出 → 交給 dispatcher 中止
        meta.update(chosen=UNTRIAGEABLE, untriageable=True, reason=reason)
        return UNTRIAGEABLE, meta
    if chosen not in profiles:                # J4 軸 B:未定義才 fail-safe 回 current
        meta.update(chosen=profile.name, error=f"chosen={chosen!r} 未定義")
        log.warning("select chosen=%r 未定義 → fallback %s", chosen, profile.name)
        return profile.name, meta
    meta.update(chosen=chosen, reason=reason)
    return chosen, meta


def select_profile(ticket: Ticket, profile, profiles: dict,
                   clearquest_id: str | None = None) -> tuple[str, dict]:
    """J4 遞歸 select:每層 `_select_once`(軸 B:可回任何 profile),鏈到葉節點。
    回 (final_name, meta{original, chosen, method, chain})。終止:無 select(葉)/回自己
    /繞圈/fail-safe(回 current)/notfound(中止)/第 10 層。無 select → (main, {})。"""
    if not getattr(profile, "select", None):
        return profile.name, {}
    current = profile
    chain = [current.name]
    method = None
    hit_cap = True
    while len(chain) <= _MAX_SELECT_DEPTH:
        if not getattr(current, "select", None):
            hit_cap = False
            break                                 # 葉節點
        chosen, once = _select_once(ticket, current, profiles, clearquest_id)
        method = once.get("method")
        if chosen == UNTRIAGEABLE:                # 任一層判不出 → 中止
            once["chain"] = chain
            return UNTRIAGEABLE, once
        if chosen == current.name:                # 回自己(含 fail-safe)→ 停
            hit_cap = False
            break
        if chosen in chain:                       # 繞圈保護 → 停在 current
            log.warning("select 繞圈 %s →回 %s;停在 %s",
                        chain, chosen, current.name)
            hit_cap = False
            break
        chain.append(chosen)
        current = profiles[chosen]
    if hit_cap:
        log.warning("select 到第 %d 層仍未收斂,停在 %s",
                    _MAX_SELECT_DEPTH, current.name)
    return current.name, {"method": method, "original": profile.name,
                          "chosen": current.name, "chain": chain}
