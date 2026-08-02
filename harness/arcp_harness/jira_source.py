"""Jira Cloud REST v3 source adapter. stdlib only.

The ONLY file that knows Cloud specifics (v3 endpoints, ADF rich text,
email+API-token basic auth). Moving to Jira Server/DC = replace this file;
everything upstream consumes ticket.Ticket (v5 D6b).

Endpoint note: Cloud deprecated GET /rest/api/3/search in 2025; the current
endpoint is /rest/api/3/search/jql (paged via nextPageToken). We call the new
one and fall back to the legacy path on 404/410/410-gone semantics, so the
same adapter also survives older deployments.
"""

from __future__ import annotations

import base64
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .ticket import Comment, Ticket


def _ssl_context() -> ssl.SSLContext:
    """python.org macOS builds ship without system CAs — prefer certifi when
    present, else fall back to the default context (fine on Linux/homebrew)."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()

_FIELDS = "summary,status,assignee,labels,description,updated"


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


# --------------------------------------------------------------------------- #
# Adapter
# --------------------------------------------------------------------------- #
class JiraCloudSource:
    def __init__(self, base_url: str, email: str, api_token: str,
                 timeout: float = 20.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        raw = f"{email}:{api_token}".encode()
        self._auth = "Basic " + base64.b64encode(raw).decode()
        self._ssl = _ssl_context()

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
        try:
            with urllib.request.urlopen(req, timeout=self.timeout,
                                        context=self._ssl) as resp:
                payload = resp.read()
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode()[:400]
            except Exception:
                pass
            e.msg = f"{e.msg} :: {detail}"  # surface Jira's error body
            raise
        return json.loads(payload) if payload.strip() else {}

    # -- API --------------------------------------------------------------- #
    def myself(self) -> dict:
        return self._request("GET", "/rest/api/3/myself")

    def search(self, jql: str, max_results: int = 50) -> list[Ticket]:
        params = {"jql": jql, "fields": _FIELDS, "maxResults": max_results}
        try:
            data = self._request("GET", "/rest/api/3/search/jql", params)
        except urllib.error.HTTPError as e:
            if e.code not in (404, 410):
                raise
            data = self._request("GET", "/rest/api/3/search", params)
        return [self._to_ticket(issue) for issue in data.get("issues", [])]

    def get_ticket(self, id_or_key: str | int,
                   with_comments: bool = True) -> Ticket:
        issue = self._request("GET", f"/rest/api/3/issue/{id_or_key}",
                              {"fields": _FIELDS})
        t = self._to_ticket(issue)
        if with_comments:
            t.comments.extend(self.get_comments(t.id))
        return t

    def get_comments(self, id_or_key: str | int) -> list[Comment]:
        data = self._request(
            "GET", f"/rest/api/3/issue/{id_or_key}/comment",
            {"orderBy": "created", "maxResults": 100})
        out = []
        for c in data.get("comments", []):
            author = c.get("author") or {}
            out.append(Comment(
                id=int(c["id"]),
                author=author.get("displayName", "?"),
                author_id=author.get("accountId", ""),
                body=adf_to_text(c.get("body")).strip(),
                created=c.get("created", "")))
        return out

    def add_comment(self, id_or_key: str | int, text: str) -> None:
        self._request("POST", f"/rest/api/3/issue/{id_or_key}/comment",
                      body={"body": text_to_adf(text)})

    def create_ticket(self, project_key: str, summary: str,
                      description: str = "", issue_type: str = "Task",
                      labels: list[str] | None = None) -> Ticket:
        """Dev/test helper — production tickets are created by humans (v5 D6b)."""
        fields: dict[str, Any] = {
            "project": {"key": project_key},
            "summary": summary,
            "description": text_to_adf(description),
            "issuetype": {"name": issue_type},
        }
        if labels:
            fields["labels"] = labels
        issue = self._request("POST", "/rest/api/3/issue",
                              body={"fields": fields})
        return self.get_ticket(issue["id"], with_comments=False)

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
            assignee_id=assignee.get("accountId"),
            labels=list(f.get("labels") or []),
            description=adf_to_text(f.get("description")).strip(),
            updated=f.get("updated") or "",
            raw=issue,
        )
