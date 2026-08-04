#!/usr/bin/env python3
"""W2.2 — 分區段 + hash 單元測(pytest-compatible,亦自跑)。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arcp_harness.sections import (  # noqa: E402
    Section,
    parse,
    render,
    resolve_attachments,
    section_hash,
    validate_keys,
    verify_and_restore,
)


def test_parse_no_marker():
    pre, secs = parse("just a plain description")
    assert pre == "just a plain description" and secs == []


def test_roundtrip():
    pre = "原始需求描述"
    secs = [Section("control", "template: t\nstatus: awaiting-approval",
                    updated="2026-08-05"),
            Section("human", "agent_name:")]
    pre2, secs2 = parse(render(pre, secs))
    assert pre2 == pre
    assert len(secs2) == 2
    assert secs2[0].owner == "control" and secs2[0].updated == "2026-08-05"
    assert "template: t" in secs2[0].body
    assert secs2[1].owner == "human"


def test_hash_normalization_stable():
    # 同內容、不同尾空白/空行 → 同 hash
    assert section_hash("a: 1\nb: 2") == section_hash("a: 1  \nb: 2\n\n")
    assert section_hash("a: 1") != section_hash("a: 2")


def test_machine_has_hash_human_none():
    _, secs = parse(render("", [Section("control", "x: 1"),
                                Section("human", "y: 2")]))
    ctrl = next(s for s in secs if s.owner == "control")
    hum = next(s for s in secs if s.owner == "human")
    assert ctrl.hash is not None
    assert hum.hash is None


def test_verify_restore_tampered_machine():
    auth = Section("control", "template: good\nstatus: ok")
    tampered = Section("control", "template: HACKED",
                       hash=section_hash("template: good\nstatus: ok"))
    restored, viol = verify_and_restore([tampered], {"control": auth})
    assert viol == ["control"]
    assert restored[0].body == "template: good\nstatus: ok"


def test_verify_respects_human():
    human = Section("human", "agent_name: whatever")  # human 無 hash
    restored, viol = verify_and_restore([human], {})
    assert viol == [] and restored[0].body == "agent_name: whatever"


def test_verify_untampered_machine_kept():
    body = "a: 1"
    ok = Section("control", body, hash=section_hash(body))
    restored, viol = verify_and_restore(
        [ok], {"control": Section("control", "auth")})
    assert viol == [] and restored[0].body == body   # hash 符,不還原


def test_attach_resolve():
    s = Section("agent:rev", "result: passed\nlog_file: attach:build.txt")
    assert resolve_attachments(s) == {"log_file": "build.txt"}


def test_validate_keys():
    assert validate_keys(Section("human", "agent_name: x\nparam1: y")) == []
    bad = validate_keys(Section("human", "AgentName: x"))
    assert "AgentName" in bad


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
    print("test-sections:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
