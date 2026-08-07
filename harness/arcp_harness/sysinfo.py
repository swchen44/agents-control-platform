"""W6.1 — Server 頁系統資訊(純 stdlib + subprocess,零外部依賴)。

原則:server 資訊(版本/資源/健康)+ 登入**狀態**(絕不顯示金鑰值)。
macOS 為主(sysctl/vm_stat/sw_vers),Linux best-effort fallback(/proc)。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time


def _sh(cmd: list[str], timeout: float = 5.0) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout)
        return r.stdout.strip()
    except Exception:  # noqa: BLE001 — 拿不到就空字串,不擋頁面
        return ""


def _versions() -> dict:
    if sys.platform == "darwin":
        os_ver = f"macOS {_sh(['sw_vers', '-productVersion'])}"
    else:
        os_ver = _sh(["uname", "-o"]) or sys.platform
    return {
        "os": os_ver,
        "kernel": _sh(["uname", "-mrs"]),
        "python": sys.version.split()[0],
        "claude": _sh(["claude", "--version"])[:60],
        "codex": _sh(["codex", "--version"])[:60],
    }


def _auth() -> dict:
    """只回登入/金鑰**狀態**(有無/檔案存在),絕不回值(安全底線 W32)。"""
    home = os.path.expanduser("~")
    codex_auth = os.path.join(home, ".codex", "auth.json")
    # claude:登入態存 ~/.claude(有 .credentials.json 或設定即視為已設定)
    claude_dir = os.path.join(home, ".claude")
    claude_login = (os.path.exists(os.path.join(claude_dir,
                                                ".credentials.json"))
                    or os.path.isdir(claude_dir))
    env_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    return {
        "codex_logged_in": os.path.exists(codex_auth),
        "claude_configured": claude_login,
        "anthropic_api_key_env": env_key,   # 只回布林
    }


def _uptime_sec() -> float:
    if sys.platform == "darwin":
        # kern.boottime: { sec = 1699..., usec = ... }
        bt = _sh(["sysctl", "-n", "kern.boottime"])
        try:
            sec = int(bt.split("sec =")[1].split(",")[0].strip())
            return max(0.0, time.time() - sec)
        except (IndexError, ValueError):
            return 0.0
    try:
        with open("/proc/uptime") as f:
            return float(f.read().split()[0])
    except OSError:
        return 0.0


def _mem() -> dict:
    """回 {total, used, free}(bytes,best-effort)。"""
    if sys.platform == "darwin":
        total = 0
        try:
            total = int(_sh(["sysctl", "-n", "hw.memsize"]) or 0)
        except ValueError:
            total = 0
        # vm_stat:page 數 × page size;free ≈ free+inactive+speculative
        vm = _sh(["vm_stat"])
        page = 4096
        free_pages = 0
        for line in vm.splitlines():
            if "page size of" in line:
                try:
                    page = int(line.split("page size of")[1]
                               .split("bytes")[0].strip())
                except (IndexError, ValueError):
                    pass
            for tag in ("Pages free", "Pages inactive", "Pages speculative"):
                if line.startswith(tag + ":"):
                    try:
                        free_pages += int(line.split(":")[1].strip()
                                          .rstrip("."))
                    except (IndexError, ValueError):
                        pass
        free = free_pages * page
        return {"total": total, "free": free,
                "used": max(0, total - free) if total else 0}
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, _, v = line.partition(":")
                info[k] = int(v.strip().split()[0]) * 1024  # kB→B
        total = info.get("MemTotal", 0)
        free = info.get("MemAvailable", info.get("MemFree", 0))
        return {"total": total, "free": free, "used": max(0, total - free)}
    except OSError:
        return {"total": 0, "free": 0, "used": 0}


def collect() -> dict:
    """Server 頁單一資料源。"""
    try:
        load = os.getloadavg()
    except OSError:
        load = (0.0, 0.0, 0.0)
    cpus = os.cpu_count() or 1
    du = shutil.disk_usage(os.path.abspath("."))
    mem = _mem()
    anomalies = []
    if du.total and du.free / du.total < 0.10:
        anomalies.append(f"磁碟空間不足(剩 {du.free / 1e9:.0f}GB / "
                         f"{du.total / 1e9:.0f}GB)")
    if load[0] > cpus * 2:
        anomalies.append(f"CPU 負載偏高(load {load[0]:.1f} / {cpus} cores)")
    if mem["total"] and mem["free"] / mem["total"] < 0.05:
        anomalies.append("可用記憶體偏低(<5%)")
    return {
        "ts": time.time(),
        "versions": _versions(),
        "auth": _auth(),
        "resources": {
            "loadavg": [round(x, 2) for x in load],
            "cpus": cpus,
            "uptime_sec": round(_uptime_sec()),
            "mem": mem,
            "disk": {"total": du.total, "free": du.free, "used": du.used},
            "cwd": os.path.abspath("."),
        },
        "anomalies": anomalies,
    }
