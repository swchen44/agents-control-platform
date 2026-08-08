"""W11.3 互動表單服務(獨立進程/port,人面向 + 一次性 token 授權)。

取代「人直接編 Jira description」——人開 comment 內的一次性連結 → 受控表單 → 送出後
系統回寫(回寫/觸發 resume 為 W11.4 整合,本檔以 on_submit 回呼掛勾)。安全模型與唯讀
dashboard、內部 control_api 分離:token 即 capability。純 stdlib、零外部依賴、可內網
桌機/行動瀏覽。

Jira 異常(使用者定調,不做 work queue):開表單先測 Jira 健康,異常 → 明示「請先檢視、
暫勿送出」但仍可看可填;真送出時若異常 → 回「稍後再試」且**不落地**(避免不同步)。
"""

from __future__ import annotations

import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .interaction import (
    EXPIRED,
    FORM_SCHEMAS,
    PENDING,
    SUBMITTED,
    validate_submission,
)
from .logutil import get_logger

log = get_logger("form")

_CSS = """
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,BlinkMacSystemFont,
"Segoe UI",Roboto,"Noto Sans TC",sans-serif;background:#f3f1ea;color:#2b2924;
line-height:1.55}main{max-width:640px;margin:0 auto;padding:20px 16px 48px}
.card{background:#fff;border:1px solid #e2ddd0;border-radius:12px;padding:18px 20px;
margin:14px 0;box-shadow:0 1px 3px rgba(0,0,0,.05)}h1{font-size:20px;margin:6px 0}
h2{font-size:15px;margin:14px 0 6px}.ctx{font-size:13px;color:#6b6558}
.ctx b{color:#2b2924}
label{display:block;font-size:13px;font-weight:600;margin:12px 0 4px}
input,textarea,select{width:100%;padding:9px 11px;border:1px solid #cfc8b8;
border-radius:8px;font:inherit;background:#fff}textarea{min-height:96px}
.hint{font-size:12px;color:#8b857a;margin-top:3px}
button{background:#b4552f;color:#fff;border:none;border-radius:9px;padding:11px 20px;
font:inherit;font-weight:600;cursor:pointer;margin-top:16px}button:hover{filter:brightness(1.06)}
.err{background:#fdece7;border:1px solid #e8b3a3;color:#b23d1f;border-radius:8px;
padding:9px 12px;margin:10px 0;font-size:13px}.err ul{margin:4px 0 0 18px;padding:0}
.warn{background:#fff5e0;border:1px solid #e6cd8a;color:#8a6a1a;border-radius:8px;
padding:10px 12px;margin:10px 0;font-size:13px}.ok{color:#2f7d45}
.rid{font-family:ui-monospace,Menlo,monospace;font-size:11px;color:#8b857a}
@media(prefers-color-scheme:dark){body{background:#1e1c19;color:#e8e3d8}
.card{background:#26241f;border-color:#3a362e}.ctx,.hint,.rid{color:#a49d8f}
.ctx b{color:#e8e3d8}input,textarea,select{background:#1e1c19;color:#e8e3d8;
border-color:#4a453b}}
"""


def _esc(x) -> str:
    return (str(x).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _page(title: str, body: str) -> str:
    return (f"<!doctype html><html lang='zh-Hant'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{_esc(title)}</title><style>{_CSS}</style></head>"
            f"<body><main>{body}</main></body></html>")


def _field_html(f: dict, req, val=None) -> str:
    k, label, typ = f["key"], f["label"], f["type"]
    req_mark = " *" if f.get("required") else ""
    v = "" if val is None else _esc(val)
    inp = ""
    if typ == "textarea":
        inp = f"<textarea name='{k}'>{v}</textarea>"
    elif typ == "int":
        lo, hi = f.get("min", ""), f.get("max", "")
        inp = (f"<input type='number' name='{k}' value='{v}' "
               f"min='{lo}' max='{hi}'>")
    elif typ == "select":
        opts = (f.get("options")
                or (req.payload.get(f["options_from"]) if req else []) or [])
        os_html = "".join(
            f"<option value='{_esc(o)}'"
            f"{' selected' if str(o) == str(val) else ''}>{_esc(o)}</option>"
            for o in opts)
        inp = f"<select name='{k}'><option value=''>—</option>{os_html}</select>"
    else:
        inp = f"<input type='text' name='{k}' value='{v}'>"
    hint = f"<div class='hint'>{_esc(f['hint'])}</div>" if f.get("hint") else ""
    return f"<label>{_esc(label)}{req_mark}</label>{inp}{hint}"


def _ctx_html(req) -> str:
    p = req.payload or {}
    rows = [f"<div class='ctx'><b>票</b> {_esc(req.key)}"
            f" · <span class='rid'>{_esc(req.request_id)}</span></div>"]
    if p.get("title"):
        rows.append(f"<div class='ctx'><b>標題</b> {_esc(p['title'])}</div>")
    if p.get("agent_state"):
        rows.append(f"<div class='ctx'><b>Agent 狀態</b> "
                    f"{_esc(p['agent_state'])}</div>")
    if p.get("question"):
        rows.append(f"<div class='ctx'><b>本次請求</b> "
                    f"{_esc(p['question'])}</div>")
    # score_and_close:顯示 grader / agent 自評(唯讀對照)
    if p.get("grader") or p.get("agent_score") is not None:
        rows.append(
            f"<div class='ctx'><b>對照</b> grader={_esc(p.get('grader', '—'))}"
            f" · agent 自評={_esc(p.get('agent_score', '—'))}</div>")
    return "".join(rows)


def render_form_page(req, jira_up: bool = True, errors=None,
                     values=None) -> str:
    """開表單頁(pending)。jira_up=False → 顯示「暫勿送出」警示但仍可看可填。"""
    schema = FORM_SCHEMAS.get(req.schema_id) or {"title": "表單", "fields": []}
    values = values or {}
    warn = ("" if jira_up else
            "<div class='warn'>⚠️ Jira 目前異常,請先<b>檢視</b>本表單,"
            "<b>暫勿送出</b>(送出可能失敗)。</div>")
    err = ""
    if errors:
        err = ("<div class='err'>請修正:<ul>"
               + "".join(f"<li>{_esc(e)}</li>" for e in errors) + "</ul></div>")
    fields = "".join(_field_html(f, req, values.get(f["key"]))
                     for f in schema["fields"])
    body = (f"<h1>{_esc(schema.get('title'))}</h1>"
            f"<div class='card'>{_ctx_html(req)}</div>"
            f"{warn}{err}"
            f"<form method='POST' class='card'>{fields}"
            f"<button type='submit'>送出</button></form>")
    return _page(schema.get("title", "表單"), body)


def render_submitted_page(req) -> str:
    from .interaction import summarize
    s = summarize(req.schema_id, req.submission or {})
    body = (f"<h1 class='ok'>✓ 已提交</h1><div class='card'>"
            f"{_ctx_html(req)}"
            f"<h2>提交內容(唯讀)</h2><div class='ctx'>{_esc(s)}</div>"
            f"<div class='hint'>提交者 {_esc(req.submitted_by or '—')}</div>"
            f"</div>")
    return _page("已提交", body)


def render_message_page(title: str, msg: str) -> str:
    return _page(title, f"<h1>{_esc(title)}</h1>"
                        f"<div class='card'><p>{_esc(msg)}</p></div>")


def process_submission(store, req, data, jira_up: bool = True,
                       on_submit=None, now: float | None = None,
                       by: str = "") -> tuple[bool, list[str]]:
    """驗證 + 依 Jira 健康決定是否落地。→ (ok, errors)。
    Jira 異常 → 回錯、**不落地**(不做 queue,避免不同步)。"""
    now = time.time() if now is None else now
    if not req.is_open(now):
        return False, ["此請求已失效或逾期,無法提交。"]
    ok, errors, cleaned = validate_submission(req.schema_id, data, req)
    if not ok:
        return False, errors
    if not jira_up:
        return False, ["Jira 目前異常,請稍後再試(未送出,可稍後重新提交)。"]
    req.status = SUBMITTED
    req.submission = cleaned
    req.submitted_at = now
    req.submitted_by = by
    store.upsert_interaction(req)
    if on_submit is not None:
        try:
            on_submit(req)                    # W11.4:回寫 Jira + 觸發 resume
        except Exception as e:  # noqa: BLE001 — 回呼壞不吃掉使用者提交
            log.warning("on_submit 失敗 req=%s:%s", req.request_id, e)
    log.info("interaction submitted req=%s ticket=%s", req.request_id, req.key)
    return True, []


class FormServer:
    """一次性表單 HTTP 服務。jira_health_fn()→bool;on_submit(req) 為回寫/觸發掛勾。"""

    def __init__(self, store, host: str = "127.0.0.1", port: int = 8790,
                 jira_health_fn=None, on_submit=None):
        self.store = store
        self.jira_health_fn = jira_health_fn or (lambda: True)
        self.on_submit = on_submit
        api = self

        class _H(BaseHTTPRequestHandler):
            def log_message(self, fmt, *a):  # noqa: N802 — 不吐 stderr
                log.debug("http %s", fmt % a)

            def _html(self, code, html):
                b = html.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(b)))
                # token 是機密:不快取、不進共用日誌
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                self.wfile.write(b)

            def _token(self):
                parts = self.path.split("?")[0].strip("/").split("/")
                return parts[1] if len(parts) >= 2 and parts[0] == "form" \
                    else None

            def do_GET(self):  # noqa: N802
                if self.path == "/health":
                    return self._html(200, "ok")
                tok = self._token()
                if not tok:
                    return self._html(404, render_message_page(
                        "找不到", "無效的連結。"))
                req = api.store.get_interaction(tok)
                return self._html(*api._view(req))

            def do_POST(self):  # noqa: N802
                tok = self._token()
                req = api.store.get_interaction(tok) if tok else None
                if req is None:
                    return self._html(404, render_message_page(
                        "找不到", "無效的連結。"))
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n).decode("utf-8") if n else ""
                data = {k: v[0] for k, v in
                        urllib.parse.parse_qs(raw, keep_blank_values=True).items()}
                jira_up = bool(api.jira_health_fn())
                ok, errors = process_submission(
                    api.store, req, data, jira_up=jira_up,
                    on_submit=api.on_submit)
                if ok:
                    return self._html(200, render_submitted_page(req))
                # 未過:重顯表單帶錯誤 + 使用者已填值(若 Jira 異常也回填)
                return self._html(200, render_form_page(
                    req, jira_up=jira_up, errors=errors, values=data))

        self._server = ThreadingHTTPServer((host, port), _H)
        self._server.daemon_threads = True
        self._thread: threading.Thread | None = None

    def _view(self, req) -> tuple[int, str]:
        """依請求狀態決定 GET 顯示(open→表單 / submitted→唯讀 / 逾期/失效→訊息)。"""
        if req is None:
            return 404, render_message_page("找不到", "無效或不存在的連結。")
        if req.status == SUBMITTED:
            return 200, render_submitted_page(req)
        if req.status == EXPIRED or req.is_expired():
            return 410, render_message_page(
                "已逾期", "此連結已逾期。如仍需處理,請聯絡負責人重新產生。")
        if req.status != PENDING:
            return 410, render_message_page("已失效", "此連結已失效。")
        return 200, render_form_page(req, jira_up=bool(self.jira_health_fn()))

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="arcp-form", daemon=True)
        self._thread.start()
        log.info("form server on %s:%d",
                 self._server.server_address[0], self.port)

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
