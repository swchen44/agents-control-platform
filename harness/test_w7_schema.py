#!/usr/bin/env python3
"""W7.1 — Profile 新欄位(goal / max_budget_monthly_usd / est_minutes 預設)+
store.clearquest_id 欄(round-trip + 舊庫 migration)單元測(pytest-compatible)。
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arcp.profiles import (  # noqa: E402
    DEFAULT_HUMAN_MINUTES_EST,
    load_profiles,
)
from arcp.store import Store, TicketSession  # noqa: E402


def _yaml(profile_extra: str = "", loop_extra: str = "") -> str:
    fd, path = tempfile.mkstemp(suffix=".yaml")
    with os.fdopen(fd, "w") as f:
        f.write(f"""
inner_loop:
  profiles:
    p:
      workspace: {{template: empty, folder: 'tickets/{{issue_id}}'}}
      agent: {{backend: rawcli, engine: claude}}
      verify: [{{name: v, files: {{x.txt: null}}}}]
      loop: {{max_attempts: 1, on_unknown: pending{loop_extra}}}
{profile_extra}
""")
    return path


def test_goal_and_monthly_budget_load():
    prof = load_profiles(_yaml(
        profile_extra="      goal: '把登入逾時修好並補測試'",
        loop_extra=", max_budget_monthly_usd: 50.0"))["p"]
    assert prof.goal == "把登入逾時修好並補測試"
    assert prof.max_budget_monthly_usd == 50.0


def test_defaults_when_unset():
    prof = load_profiles(_yaml())["p"]
    assert prof.goal is None
    assert prof.max_budget_monthly_usd is None          # None = 不限
    assert prof.human_minutes_est is None               # 保留「未設」語意
    assert prof.est_minutes() == DEFAULT_HUMAN_MINUTES_EST == 240.0


def test_est_minutes_explicit():
    prof = load_profiles(_yaml(
        profile_extra="      human_minutes_est: 90"))["p"]
    assert prof.human_minutes_est == 90.0
    assert prof.est_minutes() == 90.0


def _sess(root, **kw):
    ws = os.path.join(root, "tickets", "1", "ws")
    os.makedirs(ws, exist_ok=True)
    base = dict(issue_id=1, key="P-1", profile="p", workspace=ws,
                session_id="sid-1", attempts=1, outcome=None,
                pending_reason=None, cost_usd=0.0)
    base.update(kw)
    return TicketSession(**base)


def test_clearquest_id_roundtrip():
    root = tempfile.mkdtemp()
    st = Store(root)
    st.upsert_session(_sess(root, clearquest_id="CR-4242"))
    got = st.get_session(1)
    assert got.clearquest_id == "CR-4242"
    # 預設 None(不填)
    st.upsert_session(_sess(root, issue_id=2, key="P-2"))
    assert st.get_session(2).clearquest_id is None
    st.close()


def test_clearquest_id_migration_on_old_db():
    """舊庫(無 clearquest_id 欄)開啟時 migration 應補欄、可讀寫。"""
    root = tempfile.mkdtemp()
    db = os.path.join(root, "harness.db")
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE ticket_session (
        issue_id INTEGER PRIMARY KEY, key TEXT NOT NULL, profile TEXT NOT NULL,
        workspace TEXT NOT NULL, session_id TEXT,
        attempts INTEGER NOT NULL DEFAULT 0, outcome TEXT, pending_reason TEXT,
        cost_usd REAL NOT NULL DEFAULT 0)""")   # 沒有 clearquest_id 的舊 schema
    con.execute("INSERT INTO ticket_session(issue_id,key,profile,workspace) "
                "VALUES (1,'P-1','p','/ws')")
    con.commit()
    con.close()
    st = Store(root)                            # 觸發 _migrate
    cols = {r[1] for r in st._db.execute("PRAGMA table_info(ticket_session)")}
    assert "clearquest_id" in cols
    got = st.get_session(1)
    assert got is not None and got.clearquest_id is None   # 舊列補 NULL
    got.clearquest_id = "CR-9"
    st.upsert_session(got)
    assert st.get_session(1).clearquest_id == "CR-9"
    st.close()


if __name__ == "__main__":
    ok = True
    for _name, _fn in list(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
                print(f"  PASS  {_name}")
            except AssertionError as e:
                ok = False
                print(f"  FAIL  {_name}: {e}")
            except Exception as e:  # noqa: BLE001
                ok = False
                print(f"  ERROR {_name}: {type(e).__name__}: {e}")
    print("test-w7-schema:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
