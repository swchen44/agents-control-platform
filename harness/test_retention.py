#!/usr/bin/env python3
"""W3.3 — retention 回收 單元測(DESIGN §3/W19;pytest-compatible,亦自跑)。

涵蓋:finished_at 由 store 自動蓋章/retry 歸零、過期刪(整個 instance 含
attempts)、未過期留、retention_days=0 不回收、非終態不動、哨值路徑安全、
刪後 workspace=(reclaimed) + journal 有記、DB migration(舊庫加欄)。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arcp.profiles import Profile  # noqa: E402
from arcp.retention import reclaim  # noqa: E402
from arcp.store import Store, TicketSession  # noqa: E402

DAY = 86400


def _profile(name="p", days=270):
    return Profile(name=name, workspace_template="empty",
                   workspace_folder="tickets/{issue_id}", skills=[],
                   agent={"backend": "rawcli"}, verify=[], max_attempts=1,
                   on_unknown="pending", retention_days=days)


def _sess(iid, ws, **kw):
    base = dict(issue_id=iid, key=f"P-{iid}", profile="p", workspace=ws,
                session_id=None, attempts=1, outcome="SUCCESS",
                pending_reason=None, cost_usd=0.0)
    base.update(kw)
    return TicketSession(**base)


def _mk_instance(root, iid):
    """建 <root>/tickets/<iid>/{ws,attempts} 假 instance,回 ws 路徑。"""
    base = os.path.join(root, "tickets", str(iid))
    ws = os.path.join(base, "ws")
    os.makedirs(ws, exist_ok=True)
    os.makedirs(os.path.join(base, "attempts"), exist_ok=True)
    open(os.path.join(ws, "x.txt"), "w").write("x")
    return ws


def test_finished_at_stamped_and_reset():
    store = Store(tempfile.mkdtemp())
    s = _sess(1, "ws")
    store.upsert_session(s)                      # 終態 → 蓋章
    t1 = store.get_session(1).finished_at
    assert t1 > 0
    store.upsert_session(store.get_session(1))   # 再寫 → 不改章
    assert store.get_session(1).finished_at == t1
    s2 = store.get_session(1)
    s2.outcome = None                            # retry:outcome 清空 → 歸零
    store.upsert_session(s2)
    assert store.get_session(1).finished_at == 0.0


def test_expired_reclaimed_whole_instance():
    root = tempfile.mkdtemp()
    store = Store(os.path.join(root, "s"))
    ws = _mk_instance(root, 1)
    now = time.time()
    sess = _sess(1, ws, finished_at=now - 271 * DAY)
    store.upsert_session(sess)
    ev = reclaim(store, {"p": _profile(days=270)}, now=now)
    assert [e["type"] for e in ev] == ["workspace_reclaimed"]
    assert not os.path.exists(os.path.dirname(ws))     # base(含 attempts)全刪
    assert store.get_session(1).workspace == "(reclaimed)"
    assert ev[0]["age_days"] >= 270


def test_not_expired_kept():
    root = tempfile.mkdtemp()
    store = Store(os.path.join(root, "s"))
    ws = _mk_instance(root, 1)
    now = time.time()
    store.upsert_session(_sess(1, ws, finished_at=now - 100 * DAY))
    assert reclaim(store, {"p": _profile(days=270)}, now=now) == []
    assert os.path.isdir(ws)


def test_zero_days_never_reclaimed():
    root = tempfile.mkdtemp()
    store = Store(os.path.join(root, "s"))
    ws = _mk_instance(root, 1)
    now = time.time()
    store.upsert_session(_sess(1, ws, finished_at=now - 9999 * DAY))
    assert reclaim(store, {"p": _profile(days=0)}, now=now) == []
    assert os.path.isdir(ws)


def test_non_terminal_untouched():
    root = tempfile.mkdtemp()
    store = Store(os.path.join(root, "s"))
    ws = _mk_instance(root, 1)
    now = time.time()
    store.upsert_session(_sess(1, ws, outcome=None,
                               pending_reason="approval"))
    assert reclaim(store, {"p": _profile(days=1)}, now=now + 9999 * DAY) == []
    assert os.path.isdir(ws)


def test_sentinel_workspace_safe():
    # 哨值(adopted/handoff)非目錄:不炸、標 (reclaimed)、無 journal
    store = Store(tempfile.mkdtemp())
    now = time.time()
    store.upsert_session(_sess(1, "(adopted)", outcome="ABORTED",
                               finished_at=now - 300 * DAY))
    ev = reclaim(store, {"p": _profile(days=270)}, now=now)
    assert ev == []
    assert store.get_session(1).workspace == "(reclaimed)"


def test_unknown_profile_uses_default():
    # session 的 profile 不在 profiles(如舊 profile 已改名)→ default 270
    root = tempfile.mkdtemp()
    store = Store(os.path.join(root, "s"))
    ws = _mk_instance(root, 1)
    now = time.time()
    store.upsert_session(_sess(1, ws, profile="gone",
                               finished_at=now - 271 * DAY))
    ev = reclaim(store, {}, now=now)
    assert [e["type"] for e in ev] == ["workspace_reclaimed"]


def test_migration_old_db_gets_column():
    root = tempfile.mkdtemp()
    s1 = Store(root)
    s1.upsert_session(_sess(1, "ws", outcome=None))
    with s1._lock, s1._db:                      # 模擬舊庫:砍掉新欄
        s1._db.execute("ALTER TABLE ticket_session DROP COLUMN finished_at")
    s1.close()
    s2 = Store(root)                            # 重開 → migration 補欄
    got = s2.get_session(1)
    assert got is not None and got.finished_at == 0.0


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
    print("test-retention:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
