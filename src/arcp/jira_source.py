"""Jira source adapter — Cloud(REST v3)+ Data Center(REST v2)。stdlib only.

The ONLY file that knows Jira specifics; everything upstream consumes
ticket.Ticket (v5 D6b). **Cloud vs DC 差異一覽 = 頂部 `_FLAVOR` 表**(主題 L):
api 版本、user 識別欄位(accountId vs name)、@mention 語法、user-search 參數。
邏輯性差異(search 端點/認證/ADF vs 純文字)在對應方法內 if flavor。
設計正本:docs/design/jira-dc.md。

認證:cloud = email+API token(Basic);dc = **PAT(Bearer,8.14+)**優先,
或 username+password(Basic)——由「flavor=dc 且 user 為空」判定走 PAT
(見 config.jira_credentials 的回傳約定)。

Endpoint note: Cloud deprecated GET /rest/api/3/search in 2025; the current
endpoint is /rest/api/3/search/jql (paged via nextPageToken). We call the new
one and fall back to the legacy path on 404/410 semantics. DC 一律走
/rest/api/2/search(startAt/total 分頁;harness 每輪只取一頁,與 Cloud 對稱)。
"""

from __future__ import annotations

import base64
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .logutil import get_logger
from .ticket import Comment, Ticket

log = get_logger("jira")


def _ssl_context() -> ssl.SSLContext:
    """python.org macOS builds ship without system CAs — prefer certifi when
    present, else fall back to the default context (fine on Linux/homebrew)."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()

_FIELDS = "summary,status,assignee,labels,description,updated"

# 主題 L:Cloud vs DC 差異表(一覽無遺;方法內讀表,邏輯性差異才 if flavor)
_FLAVOR = {
    "cloud": {"api": "3", "uid": "accountId",
              "mention": "[~accountid:{}]", "usearch": "query"},
    "dc":    {"api": "2", "uid": "name",
              "mention": "[~{}]", "usearch": "username"},
}


# --------------------------------------------------------------------------- #
# ADF (Atlassian Document Format) — minimal flatten/build
# --------------------------------------------------------------------------- #
def adf_to_text(node: Any) -> str:
    """Flatten an ADF tree to plain text (best effort, lossy by design)."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(adf_to_text(n) for n in node)
    if isinstance(node, dict):
        if node.get("type") == "text":
            return node.get("text", "")
        if node.get("type") == "hardBreak":
            return "\n"
        inner = adf_to_text(node.get("content", []))
        # block-level nodes end with a newline so paragraphs stay separated
        if node.get("type") in ("paragraph", "heading", "listItem",
                                "codeBlock", "blockquote"):
            return inner + "\n"
        return inner
    return ""


def text_to_adf(text: str) -> dict:
    """One paragraph per line — enough for harness comments."""
    return {
        "type": "doc", "version": 1,
        "content": [
            {"type": "paragraph",
             "content": [{"type": "text", "text": line}] if line else []}
            for line in text.split("\n")
        ],
    }


def mention_tag_of(source, uid: str) -> str:
    """@mention 標記,flavor-aware:source.mention_tag 有則用(cloud=
    [~accountid:x]、dc=[~username]);mock/舊 source 沒有 → cloud 語法
    fallback。空 uid → ""。hil/scoring/commands 共用(主題 L3)。"""
    if not uid:
        return ""
    fn = getattr(source, "mention_tag", None)
    return fn(uid) if fn else f"[~accountid:{uid}]"


# --------------------------------------------------------------------------- #
# Adapter
# --------------------------------------------------------------------------- #
class JiraCloudSource:
    def __init__(self, base_url: str, email: str, api_token: str,
                 timeout: float = 20.0, write_retry_max: int = 5,
                 write_retry_base: float = 1.0, flavor: str = "cloud"):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        if flavor not in _FLAVOR:
            raise ValueError(f"unknown jira flavor {flavor!r}"
                             f"(expected {'/'.join(_FLAVOR)})")
        self.flavor = flavor
        self.f = _FLAVOR[flavor]
        self._api = "/rest/api/" + self.f["api"]
        # A3 (N8): only WRITES back off on rate-limit/5xx — reads are idempotent
        # and the poll loop retries them next cycle, so reads never back off.
        self._write_retry_max = max(0, write_retry_max)
        self._write_retry_base = write_retry_base
        if flavor == "dc" and not email:          # PAT(Personal Access Token)
            self._auth = "Bearer " + api_token
        else:                                     # cloud email:token / dc 帳密
            raw = f"{email}:{api_token}".encode()
            self._auth = "Basic " + base64.b64encode(raw).decode()
        self._ssl = _ssl_context()
        # W6.7:harness→Jira 寫入回呼(留言/assign/transition/description);
        # run_poller 接成 store.journal("jira_write",…),供事件時間軸顯示
        # 「HH:MM 留言 Jira / 改 assignee / transition」。None = 不記(測試預設)。
        self.on_write = None

    def _notify_write(self, action: str, id_or_key, detail: str = "") -> None:
        """寫入成功後記一筆 jira_write;回呼壞掉絕不影響 Jira 寫入本身。"""
        if not self.on_write:
            return
        try:
            self.on_write(action, id_or_key, detail)
        except Exception as e:  # noqa: BLE001
            log.warning("on_write 回呼失敗(%s %s):%s", action, id_or_key, e)

    # -- transport --------------------------------------------------------- #
    def _request(self, method: str, path: str,
                 params: dict | None = None, body: dict | None = None) -> Any:
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": self._auth,
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        write = method.upper() != "GET"
        attempt = 0
        while True:
            try:
                with urllib.request.urlopen(req, timeout=self.timeout,
                                            context=self._ssl) as resp:
                    payload = resp.read()
                break
            except urllib.error.HTTPError as e:
                # A3 (W3): back off + retry ONLY writes, ONLY on 429/5xx.
                # Reads are idempotent — the poll loop retries them next cycle.
                if (write and e.code in (429, 500, 502, 503, 504)
                        and attempt < self._write_retry_max):
                    delay = (self._retry_after(e)
                             or self._write_retry_base * (2 ** attempt))
                    attempt += 1
                    time.sleep(delay)
                    continue
                detail = ""
                try:
                    detail = e.read().decode()[:400]
                except Exception:
                    pass
                e.msg = f"{e.msg} :: {detail}"  # surface Jira's error body
                raise
        return json.loads(payload) if payload.strip() else {}

    @staticmethod
    def _retry_after(e: urllib.error.HTTPError) -> float | None:
        """Honour Jira's Retry-After header (integer seconds) when present."""
        try:
            ra = e.headers.get("Retry-After") if e.headers else None
        except Exception:
            ra = None
        if ra and str(ra).strip().isdigit():
            return float(str(ra).strip())
        return None

    # -- API --------------------------------------------------------------- #
    def myself(self) -> dict:
        return self._request("GET", self._api + "/myself")

    def my_uid(self) -> str:
        """機器人自身識別碼(cloud=accountId、dc=name/username)。"""
        return (self.myself() or {}).get(self.f["uid"], "") or ""

    def mention_tag(self, uid: str) -> str:
        """@mention 標記(cloud=[~accountid:x]、dc=[~username])。空 → ""。"""
        return self.f["mention"].format(uid) if uid else ""

    def find_account_id(self, email: str) -> str | None:
        """email → **使用者識別碼**(cloud=accountId、dc=name/username)。
        名稱維持歷史相容(K 期呼叫端/mock 都用此名);語意別名 find_user_id。
        優先 emailAddress 精確比對;隱藏 email(GDPR/DC 隱私)時唯一命中則
        取之。解析不到 → None(呼叫端當填表錯誤/換 fallback)。"""
        users = self._request(
            "GET", f"{self._api}/user/search?{self.f['usearch']}="
                   + urllib.parse.quote(email))
        if not isinstance(users, list):
            return None
        exact = [u for u in users
                 if (u.get("emailAddress") or "").lower() == email.lower()]
        pool = exact or (users if len(users) == 1 else [])
        return pool[0].get(self.f["uid"]) if pool else None

    find_user_id = find_account_id                # 語意別名(新 code 用這個)

    def search(self, jql: str, max_results: int = 50) -> list[Ticket]:
        params = {"jql": jql, "fields": _FIELDS, "maxResults": max_results}
        if self.flavor == "dc":            # DC:api/2 search(startAt/total 分頁)
            data = self._request("GET", self._api + "/search", params)
            return [self._to_ticket(i) for i in data.get("issues", [])]
        try:                               # Cloud:新端點 → 404/410 退舊端點
            data = self._request("GET", "/rest/api/3/search/jql", params)
        except urllib.error.HTTPError as e:
            if e.code not in (404, 410):
                raise
            data = self._request("GET", "/rest/api/3/search", params)
        return [self._to_ticket(issue) for issue in data.get("issues", [])]

    def get_ticket(self, id_or_key: str | int,
                   with_comments: bool = True) -> Ticket:
        issue = self._request("GET", f"{self._api}/issue/{id_or_key}",
                              {"fields": _FIELDS})
        t = self._to_ticket(issue)
        if with_comments:
            t.comments.extend(self.get_comments(t.id))
        return t

    def get_comments(self, id_or_key: str | int) -> list[Comment]:
        data = self._request(
            "GET", f"{self._api}/issue/{id_or_key}/comment",
            {"orderBy": "created", "maxResults": 100})
        out = []
        for c in data.get("comments", []):
            author = c.get("author") or {}
            out.append(Comment(
                id=int(c["id"]),
                author=author.get("displayName", "?"),
                author_id=author.get(self.f["uid"], ""),
                body=adf_to_text(c.get("body")).strip(),
                created=c.get("created", "")))
        return out

    def add_comment(self, id_or_key: str | int, text: str) -> None:
        self._request("POST", f"{self._api}/issue/{id_or_key}/comment",
                      body={"body": text_to_adf(text)})
        self._notify_write("comment", id_or_key, text)

    def add_comment_adf(self, id_or_key: str | int, adf_doc: dict,
                        detail: str = "") -> None:
        """貼一則已組好的 ADF comment(交付物用結構化 ADF,見 arcp/adf.py)。"""
        self._request("POST", f"{self._api}/issue/{id_or_key}/comment",
                      body={"body": adf_doc})
        self._notify_write("comment", id_or_key, detail or "(adf)")

    def add_attachment(self, id_or_key: str | int, filepath: str) -> dict:
        """上傳一個檔到 issue 附件(multipart;需 X-Atlassian-Token: no-check)。
        回傳 Jira 附件 metadata(含 id/filename/size/content URL)。"""
        import mimetypes
        import uuid as _uuid
        fname = os.path.basename(filepath)
        with open(filepath, "rb") as f:
            data = f.read()
        ctype = mimetypes.guess_type(fname)[0] or "application/octet-stream"
        boundary = "----arcp" + _uuid.uuid4().hex
        crlf = b"\r\n"
        body = (
            b"--" + boundary.encode() + crlf
            + ('Content-Disposition: form-data; name="file"; filename="%s"'
               % fname).encode() + crlf
            + ("Content-Type: %s" % ctype).encode() + crlf + crlf
            + data + crlf
            + b"--" + boundary.encode() + b"--" + crlf)
        url = self.base_url + f"{self._api}/issue/{id_or_key}/attachments"
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Authorization": self._auth,
            "Accept": "application/json",
            "X-Atlassian-Token": "no-check",      # Jira 附件端點必需
            "Content-Type": "multipart/form-data; boundary=" + boundary,
        })
        with urllib.request.urlopen(req, timeout=self.timeout,
                                    context=self._ssl) as resp:
            payload = resp.read()
        self._notify_write("attachment", id_or_key, fname)
        out = json.loads(payload) if payload.strip() else []
        return out[0] if isinstance(out, list) and out else {}

    def create_ticket(self, project_key: str, summary: str,
                      description: str = "", issue_type_id: str = "10003",
                      labels: list[str] | None = None) -> Ticket:
        """Dev/test helper — production tickets are created by humans (v5 D6b).

        issue_type_id (not name): names are locale data (「任務」/Task/Tarefa);
        the id (10003 = the standard Task type) is stable (lesson #4/§6-19).
        """
        fields: dict[str, Any] = {
            "project": {"key": project_key},
            "summary": summary,
            "description": text_to_adf(description),
            "issuetype": {"id": issue_type_id},
        }
        if labels:
            fields["labels"] = labels
        issue = self._request("POST", self._api + "/issue",
                              body={"fields": fields})
        return self.get_ticket(issue["id"], with_comments=False)

    def transition(self, id_or_key: str | int, to_category: str,
                   prefer_status: str | None = None) -> bool:
        """Move an issue to a transition. If prefer_status is given, first try a
        transition whose target status NAME matches it (e.g. "Cancelled" —
        workflow-specific); otherwise / if absent, fall back to the first
        transition whose target statusCategory matches (new|indeterminate|done,
        locale-immune)."""
        data = self._request("GET",
                             f"{self._api}/issue/{id_or_key}/transitions")
        trs = data.get("transitions", [])
        if prefer_status:                        # 優先按狀態名(取消狀態,workflow 相關)
            for tr in trs:
                if (tr["to"].get("name") or "").strip().lower() \
                        == prefer_status.strip().lower():
                    self._request("POST",
                                 f"{self._api}/issue/{id_or_key}/transitions",
                                 body={"transition": {"id": tr["id"]}})
                    self._notify_write("transition", id_or_key, prefer_status)
                    return True
            log.info("transition %s:找不到狀態 %r,退回 %s-category",
                     id_or_key, prefer_status, to_category)
        for tr in trs:
            if tr["to"]["statusCategory"]["key"] == to_category:
                self._request("POST",
                             f"{self._api}/issue/{id_or_key}/transitions",
                             body={"transition": {"id": tr["id"]}})
                self._notify_write("transition", id_or_key, to_category)
                return True
        return False

    def set_description(self, id_or_key: str | int, text: str) -> None:
        """Overwrite the issue description (W2.3 審批門寫分區段 plan)。"""
        self._request("PUT", f"{self._api}/issue/{id_or_key}",
                      body={"fields": {"description": text_to_adf(text)}})
        self._notify_write("description", id_or_key, "更新 description")

    def assign(self, id_or_key: str | int, account_id: str | None) -> None:
        """Set assignee(cloud={"accountId"} / dc={"name"};None=取消指派)。"""
        self._request("PUT", f"{self._api}/issue/{id_or_key}/assignee",
                      body={self.f["uid"]: account_id})
        self._notify_write("assign", id_or_key, account_id or "(取消指派)")

    def add_watcher(self, id_or_key: str | int, account_id: str) -> None:
        """加 watcher(關注者)(K:開票時把 profile.approver 加關注)。
        Jira watcher API 特別:POST body 是**裸識別碼字串**(非物件)——
        cloud=accountId、dc=username,兩者同形。"""
        self._request("POST", f"{self._api}/issue/{id_or_key}/watchers",
                      body=account_id)
        self._notify_write("watcher", id_or_key, account_id)

    # -- mapping ------------------------------------------------------------ #
    def _to_ticket(self, issue: dict) -> Ticket:
        f = issue.get("fields", {}) or {}
        assignee = f.get("assignee") or {}
        return Ticket(
            id=int(issue["id"]),
            key=issue.get("key", ""),
            summary=f.get("summary") or "",
            state=((f.get("status") or {}).get("name")) or "",
            assignee=assignee.get("displayName"),
            assignee_id=assignee.get(self.f["uid"]),
            labels=list(f.get("labels") or []),
            description=adf_to_text(f.get("description")).strip(),
            updated=f.get("updated") or "",
            raw=issue,
        )
