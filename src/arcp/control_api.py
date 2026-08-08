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
  POST /recover → poller.degraded=False(W11:管理者手動解除 Jira 降級;poll 成功
                  也會自動解除)
  POST /shutdown→ poller.stopping=True(W4.5 graceful:當前 poll 輪——含正在跑
                  的 attempt / 壓縮打包——自然跑完後退出並清理;強制關閉語意
                  見 DESIGN_hotreload.md)
  POST /evict/<id>       → 寫 EVICT 檔,agent 看門狗 killpg(W5.3 異常處置;
                           active 才准,不耗 attempt,下輪 resume)
  POST /gen_transcript/<id> → 被動產 transcript final HTML(W6.4;完成/等人/
                           進行中皆可,reason=manual)

安全:預設綁 127.0.0.1(本機控制),無認證——不可綁公網。
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .logutil import get_logger

log = get_logger("control")


class ControlAPI:
    def __init__(self, poller, store, reload_fn=None,
                 host: str = "127.0.0.1", port: int = 8787,
                 profiles_fn=None):
        self.poller = poller
        self.store = store
        self.reload_fn = reload_fn
        self.profiles_fn = profiles_fn     # W6.4:被動產 transcript 查 engine
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
                if self.path == "/recover":        # W11:管理者手動解除降級
                    api.poller.degraded = False
                    log.info("control: recover(手動解除 Jira 降級)")
                    return self._json(200, {"degraded": False})
                if self.path.startswith("/evict/"):  # W5.3 E3 實時 killpg
                    try:
                        iid = int(self.path.rsplit("/", 1)[1])
                    except ValueError:
                        return self._json(400, {"error": "bad issue id"})
                    s = api.store.get_session(iid)
                    if (s is None or s.outcome
                            or s.workspace.startswith("(")):
                        return self._json(404,
                                          {"error": "no active session"})
                    artifacts = os.path.join(
                        os.path.dirname(s.workspace), "attempts")
                    os.makedirs(artifacts, exist_ok=True)
                    with open(os.path.join(artifacts, "EVICT"), "w") as f:
                        f.write("evict")
                    log.info("control: evict %s(%s)", iid, s.key)
                    return self._json(200, {"evicted": iid})
                if self.path.startswith("/gen_transcript/"):  # W6.4 被動按鈕
                    try:
                        iid = int(self.path.rsplit("/", 1)[1])
                    except ValueError:
                        return self._json(400, {"error": "bad issue id"})
                    return self._json(*api.gen_transcript(iid))
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

    def gen_transcript(self, iid: int) -> tuple[int, dict]:
        """W6.4 被動:人在 Jira ticket 頁按「產生 transcript」→ 對當前 session
        定格 final HTML(不打包)。完成/等人/進行中皆可(只要 session_id 在且
        workspace 非哨值)。reason=manual 記入 meta.json + journal。"""
        from .transcript import engine_of_agent
        from .transcript import finalize as _finalize
        s = self.store.get_session(iid)
        if s is None or not s.session_id or s.workspace.startswith("("):
            return 404, {"error": "no transcript-able session"}
        profiles = self.profiles_fn() if self.profiles_fn else {}
        prof = (profiles or {}).get(s.profile)
        engine = engine_of_agent(prof.agent) if prof is not None else "claude"
        try:
            outs = _finalize(s.session_id, engine, s.workspace,
                             pack=False, reason="manual")
        except Exception as e:  # noqa: BLE001 — 渲染壞不弄死 API
            log.warning("control: gen_transcript %s 失敗:%s", iid, e)
            return 500, {"error": str(e)}
        if not outs:
            return 500, {"error": "render 無產出(renderer 缺席或 session 檔已清)"}
        self.store.journal("transcript_packed", iid, s.key,
                           reason="manual", files=[os.path.basename(a)
                                                   for a in outs])
        log.info("control: gen_transcript %s(%s)→ %d 檔", iid, s.key, len(outs))
        return 200, {"generated": iid, "files": len(outs)}

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
            "degraded": bool(getattr(self.poller, "degraded", False)),  # W11

            "in_flight": len(self.store.active_sessions()),
            "queued": sum(1 for s in sessions if s.queued),
            "inactive": sum(1 for s in sessions if s.inactive),
            "sessions": len(sessions),
            "outcomes": outcomes,
            "pending": pending,
            "cost_usd": round(sum(s.cost_usd or 0 for s in sessions), 4),
            # W9.1:poll 統計(已 poll 幾次 / 起始 / 間隔)
            "poll_count": int(getattr(self.poller, "_cycles", 0)),
            "started_at": float(getattr(self.poller, "started_at", 0.0)),
            "poll_interval": float(getattr(self.poller, "poll_interval", 0.0)),
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
