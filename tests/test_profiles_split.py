#!/usr/bin/env python3
"""Q15 profile 拆檔:load_profiles 合併主檔 inline + config/profiles/<名>.yaml
(檔名=名、body=內容);同名衝突 fail-fast;source_yaml 記來源。免 token。"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from arcp.profiles import load_profiles  # noqa: E402
from arcp.routing import ConfigError  # noqa: E402

ok = fail = 0


def check(name, cond):
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS  {name}")
    else:
        fail += 1; print(f"  FAIL  {name}")


_BODY = """\
workspace: { template: empty, folder: 'tickets/{issue_id}' }
agent: { backend: rawcli, engine: claude, model: haiku }
verify:
  - name: done
    files: { DONE.md: }
loop: { max_attempts: 2, on_unknown: pending }
"""


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


# ── 主檔 inline + profiles/ 合併 ──────────────────────────────────── #
d = tempfile.mkdtemp()
main = os.path.join(d, "config.yaml")
_write(main, "inner_loop:\n  profiles:\n    inbuilt:\n"
       + "\n".join("      " + ln for ln in _BODY.splitlines()) + "\n")
_write(os.path.join(d, "profiles", "split_a.yaml"), _BODY)
_write(os.path.join(d, "profiles", "split_b.yaml"), _BODY)
_write(os.path.join(d, "profiles", "README.md"), "# not a profile\n")
profs = load_profiles(main)
check("合併:inline + 兩個拆檔都在", set(profs) == {"inbuilt", "split_a", "split_b"})
check("拆檔:檔名=profile 名", "split_a" in profs and profs["split_a"].name == "split_a")
check("拆檔:README.md 被略過(非 profile)", "README" not in profs)
check("source_yaml:inline → 主檔",
      profs["inbuilt"].source_yaml.endswith("config.yaml"))
check("source_yaml:拆檔 → 該檔",
      profs["split_a"].source_yaml.endswith(os.path.join("profiles", "split_a.yaml")))

# ── 同名衝突(主檔 + 拆檔同名)→ fail-fast ─────────────────────────── #
d2 = tempfile.mkdtemp()
main2 = os.path.join(d2, "config.yaml")
_write(main2, "inner_loop:\n  profiles:\n    dup:\n"
       + "\n".join("      " + ln for ln in _BODY.splitlines()) + "\n")
_write(os.path.join(d2, "profiles", "dup.yaml"), _BODY)
try:
    load_profiles(main2); r = False
except ConfigError:
    r = True
check("同名跨檔衝突 → ConfigError", r)

# ── 無 profiles/ 子夾 → 只有 inline(相容)────────────────────────── #
d3 = tempfile.mkdtemp()
main3 = os.path.join(d3, "config.yaml")
_write(main3, "inner_loop:\n  profiles:\n    only:\n"
       + "\n".join("      " + ln for ln in _BODY.splitlines()) + "\n")
profs3 = load_profiles(main3)
check("無 profiles/ 子夾 → 只有 inline", set(profs3) == {"only"})

print(f"test-profiles-split: {'PASS' if fail == 0 else 'FAIL'} ({ok}/{ok+fail})")
sys.exit(1 if fail else 0)
