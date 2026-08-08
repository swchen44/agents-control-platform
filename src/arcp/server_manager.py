"""Long-lived shared agent-server manager (conc.3, N1/N4).

Runs ONE agent-server subprocess that all openhands-server backend tickets
share (vs每 attempt 自起). Health-checks + restarts it (same OH_PERSISTENCE_DIR
→ OpenHands rehydrates conversations from base_state.json), so a server crash
does not lose in-flight tickets: they resume next poll via session_id.

stdlib only. The server itself runs in the openhands venv (spawned via
`venv_python -m openhands.agent_server`); this manager lives in the harness
(system python) and only supervises the subprocess.
"""

from __future__ import annotations

import os
import subprocess
import time
import urllib.error
import urllib.request

HOST = "127.0.0.1"


class ServerManager:
    def __init__(self, venv_python: str, persist_dir: str,
                 port: int = 18010, api_key: str = "harness-shared-key"):
        self.venv_python = os.path.abspath(venv_python)
        self.persist_dir = os.path.abspath(persist_dir)
        self.port = port
        self.api_key = api_key
        self.base_url = f"http://{HOST}:{port}"
        self._proc: subprocess.Popen | None = None
        self._log_path = os.path.join(self.persist_dir, "server.log")

    # -- lifecycle --------------------------------------------------------- #
    def _spawn(self) -> None:
        os.makedirs(self.persist_dir, exist_ok=True)
        env = dict(os.environ)
        env["OH_SESSION_API_KEYS_0"] = self.api_key
        env["OH_PERSISTENCE_DIR"] = self.persist_dir      # same dir → rehydrate
        env["OPENHANDS_SUPPRESS_BANNER"] = "1"
        log = open(self._log_path, "a")
        self._proc = subprocess.Popen(
            [self.venv_python, "-m", "openhands.agent_server",
             "--host", HOST, "--port", str(self.port)],
            env=env, stdout=log, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL)

    def _healthy(self, timeout: float = 5) -> bool:
        try:
            with urllib.request.urlopen(self.base_url + "/", timeout=timeout):
                return True
        except (urllib.error.URLError, OSError):
            return False

    def _wait_ready(self, deadline_s: float = 120) -> bool:
        end = time.time() + deadline_s
        while time.time() < end:
            if self._proc and self._proc.poll() is not None:
                return False  # died during startup
            if self._healthy():
                return True
            time.sleep(1.0)
        return False

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    def ensure(self) -> bool:
        """Lazy start, or restart if crashed/unhealthy (N1). Returns ready."""
        if self._proc is None or self._proc.poll() is not None:
            self._spawn()
            return self._wait_ready()
        if not self._healthy():
            self.restart()
            return self._healthy()
        return True

    def restart(self) -> None:
        """Kill + respawn on the SAME persist dir → OpenHands rehydrates."""
        self.close()
        self._spawn()
        self._wait_ready()

    def close(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None
