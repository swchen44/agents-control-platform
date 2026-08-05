"""W2.6 — REST 控制面(W13):run_poller 內嵌後台 HTTP daemon 線程。

控制要作用於正在跑的 poller 進程,內嵌最直接(stdlib http.server,零依賴)。
端點(JSON):
  GET  /health  → {"ok": true}
  GET  /status  → paused / in_flight / queued / inactive / outcome / pending
                  計數 + cost 彙總
  POST /pause   → poller.paused=True(graceful:只 watch 不派新工,正在跑的不中斷)
  POST /resume  → poller.paused=False
  POST /reload  → reload_fn()(hot reload:範圍見 DESIGN_hotreload.md;
                  壞 config 回 400、舊設定續用、不弄死 poller)
  POST /shutdown→ poller.stopping=True(W4.5 graceful:當前 poll 輪——含正在跑
                  的 attempt / 壓縮打包——自然跑完後退出並清理;強制關閉語意
                  見 DESIGN_hotreload.md)

安全:預設綁 127.0.0.1(本機控制),無認證——不可綁公網。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .logutil import get_logger

log = get_logger("control")


class ControlAPI:
    def __init__(self, poller, store, reload_fn=None,
                 host: str = "127.0.0.1", port: int = 8787):
        self.poller = poller
        self.store = store
        self.reload_fn = reload_fn
        api = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):  # noqa: N802 — 不吐 stderr
                log.debug("http %s", fmt % args)

            def _json(self, code: int, obj) -> None:
                body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type",
                                 "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                # W2.7:dashboard(detail_server,另一 port)的 fetch 要能讀
                # 回應;API 本身仍只綁 127.0.0.1(CORS 不放寬綁定範圍)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                if self.path == "/health":
                    return self._json(200, {"ok": True})
                if self.path == "/status":
                    return self._json(200, api.status())
                return self._json(404, {"error": "not found"})

            def do_POST(self):  # noqa: N802
                if self.path == "/pause":
                    api.poller.paused = True
                    log.info("control: pause(只 watch,不派新工)")
                    return self._json(200, {"paused": True})
                if self.path == "/resume":
                    api.poller.paused = False
                    log.info("control: resume")
                    return self._json(200, {"paused": False})
                if self.path == "/shutdown":       # W4.5 graceful shutdown
                    api.poller.stopping = True
                    log.info("control: shutdown(當前輪跑完後退出)")
                    return self._json(200, {"stopping": True})
                if self.path == "/reload":
                    if api.reload_fn is None:
                        return self._json(501, {"error": "reload 未接線"})
                    try:
                        summary = api.reload_fn()
                    except Exception as e:  # 壞 config 不能弄死 poller
                        log.warning("control: reload 失敗:%s", e)
                        return self._json(400, {"error": str(e)})
                    log.info("control: reload %s", summary)
                    return self._json(200, {"reloaded": True,
                                            **(summary or {})})
                return self._json(404, {"error": "not found"})

        self._server = ThreadingHTTPServer((host, port), _Handler)
        self._server.daemon_threads = True
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        """實際綁定 port(建構傳 0 = 系統配 ephemeral,測試用)。"""
        return self._server.server_address[1]

    def status(self) -> dict:
        sessions = self.store.all_sessions()
        outcomes: dict[str, int] = {}
        pending: dict[str, int] = {}
        for s in sessions:
            if s.outcome:
                outcomes[s.outcome] = outcomes.get(s.outcome, 0) + 1
            if s.pending_reason:
                pending[s.pending_reason] = pending.get(s.pending_reason, 0) + 1
        return {
            "paused": bool(getattr(self.poller, "paused", False)),
            "stopping": bool(getattr(self.poller, "stopping", False)),
            "in_flight": len(self.store.active_sessions()),
            "queued": sum(1 for s in sessions if s.queued),
            "inactive": sum(1 for s in sessions if s.inactive),
            "sessions": len(sessions),
            "outcomes": outcomes,
            "pending": pending,
            "cost_usd": round(sum(s.cost_usd or 0 for s in sessions), 4),
        }

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="arcp-control",
            daemon=True)
        self._thread.start()
        log.info("control API listening on %s:%d",
                 self._server.server_address[0], self.port)

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
