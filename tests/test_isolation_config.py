#!/usr/bin/env python3
"""W3.6 — 隔離設定檔介面 單元測(D1/W22;pytest-compatible,亦自跑)。

涵蓋:provider 白名單 fail-fast、auto 依平台解析、舊寫法 os_sandbox 映射、
未實作 provider 降級 none(接受設定不啟用)、seatbelt 非 darwin 降級、
inner_runner job.os_sandbox 接線。
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arcp.isolation import (  # noqa: E402
    PROVIDERS,
    requested_provider,
    resolve,
)
from arcp.profiles import load_profiles  # noqa: E402
from arcp.routing import ConfigError  # noqa: E402


def _yaml(agent_extra: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".yaml")
    with os.fdopen(fd, "w") as f:
        f.write(f"""
inner_loop:
  profiles:
    p:
      workspace: {{template: empty, folder: 'tickets/{{issue_id}}'}}
      agent:
        backend: rawcli
{agent_extra}
      verify: [{{name: v, files: {{x.txt: null}}}}]
      loop: {{max_attempts: 1, on_unknown: pending}}
""")
    return path


def test_whitelist_fail_fast():
    try:
        load_profiles(_yaml("        isolation: {provider: chroot}"))
        raise AssertionError("應拒絕未知 provider")
    except ConfigError as e:
        assert "isolation.provider" in str(e)


def test_valid_providers_load():
    for p in PROVIDERS:
        profs = load_profiles(_yaml(f"        isolation: {{provider: {p}}}"))
        assert profs["p"].agent["isolation"]["provider"] == p


def test_auto_by_platform():
    cfg = {"isolation": {"provider": "auto"}}
    assert resolve(cfg, platform="darwin") == "seatbelt"
    assert resolve(cfg, platform="linux") == "none"    # landlock 未實作→降級
    assert resolve(cfg, platform="win32") == "none"    # appcontainer 未實作
    assert resolve(cfg, platform="freebsd") == "none"  # 無對應


def test_legacy_os_sandbox_maps_to_auto():
    assert requested_provider({"os_sandbox": True}) == "auto"
    assert requested_provider({"os_sandbox": False}) == "none"
    assert requested_provider({}) == "none"
    assert resolve({"os_sandbox": True}, platform="darwin") == "seatbelt"


def test_unimplemented_accepted_but_disabled():
    assert resolve({"isolation": {"provider": "docker"}},
                   platform="darwin") == "none"
    assert resolve({"isolation": {"provider": "landlock"}},
                   platform="linux") == "none"


def test_seatbelt_only_on_darwin():
    cfg = {"isolation": {"provider": "seatbelt"}}
    assert resolve(cfg, platform="darwin") == "seatbelt"
    assert resolve(cfg, platform="linux") == "none"


def test_explicit_provider_overrides_legacy():
    cfg = {"os_sandbox": True, "isolation": {"provider": "none"}}
    assert resolve(cfg, platform="darwin") == "none"   # isolation 區塊優先


def test_inner_runner_wiring():
    # job.os_sandbox 只在有效 provider=seatbelt 時為 True(darwin 上跑)
    from arcp.inner_runner import resolve_isolation
    on = resolve_isolation({"os_sandbox": True}) == "seatbelt"
    assert on == (sys.platform == "darwin")
    assert resolve_isolation({"isolation": {"provider": "none"}}) == "none"


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
    print("test-isolation-config:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
