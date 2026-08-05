"""W3.6 — 執行隔離可插拔:設定檔介面先行,不實驗(D1/W22)。

使用者定調:「未來 linux/windows/macos,優先用 OS 提供方,也給選項是 docker,
用設定檔給使用者選擇」——本波只落**介面與解析**,實作僅現有的 macOS seatbelt。

profile 寫法(agent 區塊內):

    agent:
      isolation:
        provider: auto        # auto | seatbelt | landlock | appcontainer
                              # | docker | none
      # 舊寫法向後相容:os_sandbox: true == provider: auto(deprecation 註記)

provider 語意:
  auto         依 OS 選提供方:darwin→seatbelt、linux→landlock(預留)、
               windows→appcontainer(預留)
  seatbelt     macOS sandbox-exec(已實作、已實測:workspace 可寫、外部擋)
  landlock     Linux LSM(預留,接受設定不啟用)
  appcontainer Windows(預留,接受設定不啟用)
  docker       容器隔離(預留,接受設定不啟用;邊界見 DESIGN_isolation)
  none         不隔離(codex 有自己的 --sandbox,os 層對它本就 no-op)

resolve() 回傳「有效 provider」:未實作/不適用平台 → 降級 none + WARNING log
(接受設定、不啟用——使用者要求先不驗)。
"""

from __future__ import annotations

import sys

from .logutil import get_logger

log = get_logger("isolation")

PROVIDERS = ("auto", "seatbelt", "landlock", "appcontainer", "docker", "none")
_IMPLEMENTED = {"seatbelt"}          # 目前唯一真的會啟用的提供方
_AUTO_BY_PLATFORM = {"darwin": "seatbelt", "linux": "landlock",
                     "win32": "appcontainer"}


def requested_provider(agent_cfg: dict) -> str:
    """讀 profile 設定(含舊寫法映射),不做平台解析。"""
    iso = agent_cfg.get("isolation") or {}
    provider = iso.get("provider")
    if provider is not None:
        return str(provider)
    # 向後相容:os_sandbox: true == provider auto(deprecated,文件註記)
    return "auto" if agent_cfg.get("os_sandbox") else "none"


def resolve(agent_cfg: dict, platform: str | None = None) -> str:
    """設定 → 有效 provider。auto 依 OS;未實作 → none + WARNING(不啟用)。"""
    platform = platform or sys.platform
    want = requested_provider(agent_cfg)
    if want == "auto":
        want = _AUTO_BY_PLATFORM.get(platform, "none")
    if want == "none":
        return "none"
    if want not in _IMPLEMENTED:
        log.warning("isolation provider %r 尚未實作(W22 介面先行)——"
                    "本次不啟用隔離", want)
        return "none"
    if want == "seatbelt" and platform != "darwin":
        log.warning("seatbelt 只在 macOS 可用(目前 %s)——不啟用隔離", platform)
        return "none"
    return want
