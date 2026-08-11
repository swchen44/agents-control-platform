#!/usr/bin/env python3
"""K:負責人 email 身分門禁(identity.owner_gate)。免 token、純函式。
情境:選填(無 owner)放行、負責人本人、正規化(大小寫/空白)、admin 豁免、
profile.approver 豁免、都不符拒絕、session/profile 為 None 容錯。"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from arcp.identity import normalize_email, owner_gate  # noqa: E402

ok = fail = 0


def check(name, cond):
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS  {name}")
    else:
        fail += 1; print(f"  FAIL  {name}")


def _sess(email):
    return SimpleNamespace(owner_email=email)


def _prof(approver=None):
    return SimpleNamespace(approver=approver)


ADMINS = ["Ops@Company.com", "lead@company.com"]

# ── 選填門禁:owner 為空 → 一律放行 ──────────────────────────────── #
check("owner 空 → 放行(門禁未啟用)",
      owner_gate("anyone@x.com", _sess(None), _prof(), ADMINS)[0] is True)
check("owner 空字串 → 放行",
      owner_gate("a@x.com", _sess("  "), _prof(), ADMINS)[0] is True)
check("session None → 放行",
      owner_gate("a@x.com", None, _prof(), ADMINS)[0] is True)

# ── 負責人本人(含正規化)─────────────────────────────────────── #
check("== owner → 放行", owner_gate(
    "boss@x.com", _sess("boss@x.com"), _prof(), ADMINS)[0] is True)
check("大小寫/空白正規化 → 放行", owner_gate(
    "  BOSS@X.com ", _sess("boss@x.com"), _prof(), ADMINS)[0] is True)

# ── 管理者豁免(config admin_emails,大小寫不敏感)───────────────── #
check("∈ admin_emails → 放行(大小寫不敏感)", owner_gate(
    "ops@company.com", _sess("boss@x.com"), _prof(), ADMINS)[0] is True)

# ── profile.approver 豁免 ────────────────────────────────────── #
check("== profile.approver → 放行", owner_gate(
    "rev@x.com", _sess("boss@x.com"), _prof("rev@x.com"), ADMINS)[0] is True)
check("approver 為 None 不誤放", owner_gate(
    "", _sess("boss@x.com"), _prof(None), ADMINS)[0] is False)

# ── 都不符 → 拒絕 ────────────────────────────────────────────── #
okd, msg = owner_gate("stranger@x.com", _sess("boss@x.com"), _prof(), ADMINS)
check("都不符 → 拒絕 + 有訊息", okd is False and bool(msg))
check("profile None + 非 owner/admin → 拒絕", owner_gate(
    "stranger@x.com", _sess("boss@x.com"), None, ADMINS)[0] is False)
check("admin_emails None 容錯 → 非 owner 拒絕", owner_gate(
    "x@x.com", _sess("boss@x.com"), _prof(), None)[0] is False)

# ── normalize_email ─────────────────────────────────────────── #
check("normalize:None → ''", normalize_email(None) == "")
check("normalize:strip+lower", normalize_email("  A@B.COM ") == "a@b.com")

print(f"test-identity: {'PASS' if fail == 0 else 'FAIL'} ({ok}/{ok+fail})")
sys.exit(1 if fail else 0)
