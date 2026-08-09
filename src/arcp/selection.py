"""Q16 profile 選擇(A/B 測試 / 泛化 triage)。

首次派工時,若 route 命中的 main profile 有 `select` 區塊,就從 [main + candidates]
選一個實際 profile 來跑:
  method=random → 隨機分流(A/B 測試)。
  method=script → 把 ticket/clearquest/候選 等資訊以 JSON 餵給命令的 stdin,命令在
                  stdout 回傳選中的 profile 名(可據 description/crid… 做條件式 triage)。
選中的由 dispatcher pin 進 session(resume 不重選)。任何失敗一律 fail-safe 回 main。

「選 profile」同時就是泛化的 triage:選到 require_approval=true 的 profile 就要人放行、
選到 false 的就直接跑 —— triage 與 A/B 共用同一機制。
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


def _pool(profile) -> list[str]:
    """[main] + candidates,去重保序。"""
    out = [profile.name]
    for c in (profile.select or {}).get("candidates", []):
        if c not in out:
            out.append(c)
    return out


def _script_input(ticket: Ticket, profile, pool: list[str],
                  yaml_of: dict, clearquest_id: str | None) -> dict:
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
    }


def select_profile(ticket: Ticket, profile, profiles: dict,
                   clearquest_id: str | None = None) -> tuple[str, dict]:
    """回 (chosen_profile_name, meta)。無 select → (main, {});失敗一律回 main。"""
    cfg = getattr(profile, "select", None)
    if not cfg:
        return profile.name, {}
    pool = [n for n in _pool(profile) if n in profiles]
    method = cfg.get("method", "random")
    meta = {"method": method, "pool": pool, "original": profile.name}

    if method == "random":
        chosen = random.choice(pool)       # 非密碼用途(A/B 分流)
        meta["chosen"] = chosen
        return chosen, meta

    # method == "script":JSON stdin → stdout 回 profile 名
    yaml_of = {n: (getattr(profiles[n], "source_yaml", "") or config_path())
               for n in pool}
    argv = shlex.split(cfg["script"])
    try:
        proc = subprocess.run(
            argv, input=json.dumps(_script_input(ticket, profile, pool,
                                                 yaml_of, clearquest_id)),
            capture_output=True, text=True, timeout=_SCRIPT_TIMEOUT)
    except (subprocess.TimeoutExpired, OSError) as e:
        log.warning("select script 失敗 ticket=%s: %s → fallback %s",
                    ticket.key, e, profile.name)
        meta["chosen"] = profile.name
        meta["error"] = str(e)
        return profile.name, meta
    for line in (proc.stderr or "").splitlines():
        log.info("[select:%s] %s", ticket.key, line)
    chosen = (proc.stdout or "").strip().splitlines()[-1].strip() \
        if (proc.stdout or "").strip() else ""
    if proc.returncode != 0 or chosen not in pool:
        log.warning("select script rc=%s chosen=%r 不在 pool %s → fallback %s",
                    proc.returncode, chosen, pool, profile.name)
        meta["chosen"] = profile.name
        meta["error"] = f"rc={proc.returncode} chosen={chosen!r}"
        return profile.name, meta
    meta["chosen"] = chosen
    return chosen, meta
