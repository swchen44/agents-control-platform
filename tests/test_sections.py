#!/usr/bin/env python3
"""W2.2 — 分區段 + hash 單元測(定案版面 2026-08-05;pytest-compatible,亦自跑)。

涵蓋:3-tuple parse、區塊置頂 + 結束標記、human 前置排序、區塊外(before/after)不碰、
hash 規範化、全掃描驗 hash 還原/尊重 human、attach 引用、命名校驗。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arcp.sections import (  # noqa: E402
    END_MARKER,
    MARKER,
    Section,
    parse,
    render,
    resolve_attachments,
    section_hash,
    validate_keys,
    verify_and_restore,
)


def test_parse_no_marker():
    before, secs, after = parse("just a plain description")
    assert before == "just a plain description"
    assert secs == [] and after == ""


def test_roundtrip_block_on_top():
    secs = [Section("control", "template: t\nstatus: awaiting-approval",
                    updated="2026-08-05"),
            Section("human", "agent_name:")]
    text = render("", secs, "原始需求描述")
    before, secs2, after = parse(text)
    assert before == ""
    assert after == "原始需求描述"
    by = {s.owner: s for s in secs2}
    assert set(by) == {"control", "human"}
    assert by["control"].updated == "2026-08-05"
    assert "template: t" in by["control"].body


def test_block_is_on_top_and_end_marker():
    text = render("", [Section("control", "x: 1")], "原始需求在下方")
    assert MARKER in text and END_MARKER in text
    # 版面:MARKER … END_MARKER … 原始需求(區塊整個置頂)
    assert text.index(MARKER) < text.index(END_MARKER)
    assert text.index(END_MARKER) < text.index("原始需求在下方")


def test_human_first_ordering():
    # 即使 control 先傳,render 也要把 human 排最前(方便人填)
    text = render("", [Section("control", "x: 1"), Section("human", "agent_name:")])
    assert text.index("owner=human") < text.index("owner=control")


def test_content_outside_block_untouched():
    # 區塊前後的非區段內容一律保留(此處 before 與 after 都有)
    text = render("上方筆記", [Section("control", "x: 1")], "下方原始需求")
    before, _, after = parse(text)
    assert before == "上方筆記"
    assert after == "下方原始需求"


def test_hash_normalization_stable():
    # 同內容、不同尾空白/空行 → 同 hash
    assert section_hash("a: 1\nb: 2") == section_hash("a: 1  \nb: 2\n\n")
    assert section_hash("a: 1") != section_hash("a: 2")


def test_machine_has_hash_human_none():
    _, secs, _ = parse(render("", [Section("control", "x: 1"),
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


def test_verify_full_scan_multiple_sections():
    # 全掃描:混多段,只有被誤改的機器段列入 violations 並還原,乾淨段保留
    clean = Section("control", "a: 1", hash=section_hash("a: 1"))
    dirty = Section("agent:rev", "r: HACK", hash=section_hash("r: ok"))
    human = Section("human", "agent_name: x")            # 永遠尊重
    auth = {"agent:rev": Section("agent:rev", "r: ok")}
    restored, viol = verify_and_restore([clean, dirty, human], auth)
    assert viol == ["agent:rev"]
    by = {s.owner: s for s in restored}
    assert by["control"].body == "a: 1"                  # 乾淨保留
    assert by["agent:rev"].body == "r: ok"              # 被誤改→還原
    assert by["human"].body == "agent_name: x"          # human 不動


def test_verify_no_authoritative_still_flags():
    # 被誤改但無權威版:仍列 violations(供呼叫端貼 comment),保留現況不 crash
    dirty = Section("control", "x: HACK", hash=section_hash("x: ok"))
    restored, viol = verify_and_restore([dirty], {})
    assert viol == ["control"]
    assert restored[0].body == "x: HACK"


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
