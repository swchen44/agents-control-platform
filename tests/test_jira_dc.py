#!/usr/bin/env python3
"""主題 L — Jira Data Center 相容。免網:_FLAVOR 差異表、auth 三模式
(cloud token / dc PAT Bearer / dc basic)、jira_credentials flavor 分支、
search 端點切換(mock _request)。cloud 預設行為不變。pytest 相容,亦自跑。"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from arcp.config import jira_credentials  # noqa: E402
from arcp.jira_source import (  # noqa: E402
    _FLAVOR,
    JiraCloudSource,
    mention_tag_of,
)

ok = fail = 0


def check(name, cond):
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS  {name}")
    else:
        fail += 1; print(f"  FAIL  {name}")


# ── _FLAVOR 差異表 ─────────────────────────────────────────────────── #
check("差異表:cloud/dc 鍵齊(api/uid/mention/usearch)",
      all(set(v) == {"api", "uid", "mention", "usearch"}
          for v in _FLAVOR.values()) and set(_FLAVOR) == {"cloud", "dc"})
check("差異表:dc = api/2 + name + [~username]",
      _FLAVOR["dc"]["api"] == "2" and _FLAVOR["dc"]["uid"] == "name"
      and _FLAVOR["dc"]["mention"].format("bob") == "[~bob]")
check("差異表:cloud = api/3 + accountId + [~accountid:]",
      _FLAVOR["cloud"]["api"] == "3"
      and _FLAVOR["cloud"]["mention"].format("x1") == "[~accountid:x1]")

# ── auth 三模式 + 端點版本 ─────────────────────────────────────────── #
c = JiraCloudSource("https://x.atlassian.net", "a@x.com", "tok")
check("cloud 預設:api/3 + Basic(現行為零變)",
      c.flavor == "cloud" and c._api == "/rest/api/3"
      and c._auth.startswith("Basic "))
d1 = JiraCloudSource("https://jira.corp", "", "my-pat", flavor="dc")
check("dc + user 空 → PAT Bearer + api/2",
      d1._auth == "Bearer my-pat" and d1._api == "/rest/api/2")
d2 = JiraCloudSource("https://jira.corp", "bob", "pw", flavor="dc")
check("dc + 帳密 → Basic", d2._auth.startswith("Basic "))
try:
    JiraCloudSource("https://x", "a", "b", flavor="server")
    check("未知 flavor → ValueError", False)
except ValueError:
    check("未知 flavor → ValueError", True)

# ── jira_credentials flavor 分支(tmp env 檔)──────────────────────── #
def _env(content):
    f = tempfile.NamedTemporaryFile("w", suffix=".env", delete=False)
    f.write(content); f.close()
    return f.name

p = _env("JIRA_BASE_URL=https://jira.corp\nJIRA_PAT=p123\n"
         "JIRA_USERNAME=bob\nJIRA_PASSWORD=pw\n")
check("credentials dc:PAT 優先 → user 空字串",
      jira_credentials(p, flavor="dc") == ("https://jira.corp", "", "p123"))
p2 = _env("JIRA_BASE_URL=https://jira.corp\n"
          "JIRA_USERNAME=bob\nJIRA_PASSWORD=pw\n")
check("credentials dc:無 PAT → 帳密 Basic",
      jira_credentials(p2, flavor="dc")
      == ("https://jira.corp", "bob", "pw"))
p3 = _env("JIRA_BASE_URL=https://jira.corp\n")
try:
    jira_credentials(p3, flavor="dc")
    check("credentials dc:全缺 → 報錯提示三變數", False)
except RuntimeError as e:
    check("credentials dc:全缺 → 報錯提示三變數", "JIRA_PAT" in str(e))
p4 = _env("JIRA_BASE_URL=https://x.atlassian.net\n"
          "JIRA_EMAIL=a@x.com\nJIRA_API_TOKEN=t\n")
check("credentials cloud:現行為不變",
      jira_credentials(p4) == ("https://x.atlassian.net", "a@x.com", "t"))

# ── search 端點切換(mock _request)─────────────────────────────────── #
def _spy(src):
    calls = []

    def _fake(method, path, params=None, body=None):
        calls.append(path)
        return {"issues": []}
    src._request = _fake
    return calls

calls = _spy(d1)
d1.search("project = X")
check("search dc:打 /rest/api/2/search(不走 /search/jql)",
      calls == ["/rest/api/2/search"])
calls = _spy(c)
c.search("project = X")
check("search cloud:打 /rest/api/3/search/jql(現行為)",
      calls == ["/rest/api/3/search/jql"])

# ── L2–L4:mention / user-search / assign / my_uid / _to_ticket ────── #
check("mention_tag:dc=[~username] / cloud=[~accountid:] / 空→''",
      d1.mention_tag("bob") == "[~bob]"
      and c.mention_tag("x1") == "[~accountid:x1]"
      and c.mention_tag("") == "")


class _NoTag:                                    # mock/舊 source(無 helper)
    pass


check("mention_tag_of:source 有→flavor 語法;無→cloud fallback;空→''",
      mention_tag_of(d1, "bob") == "[~bob]"
      and mention_tag_of(_NoTag(), "x1") == "[~accountid:x1]"
      and mention_tag_of(_NoTag(), "") == "")

d3 = JiraCloudSource("https://jira.corp", "", "pat", flavor="dc")
_paths, _bodies = [], []


def _fake(method, path, params=None, body=None):
    _paths.append(path); _bodies.append(body)
    if "/user/search" in path:
        return [{"name": "bob", "emailAddress": "bob@corp.com"}]
    if path.endswith("/myself"):
        return {"name": "bot1", "accountId": "should-not-use"}
    return {}


d3._request = _fake
check("find_account_id dc:username= 參數 + 回 name",
      d3.find_account_id("bob@corp.com") == "bob"
      and "username=bob%40corp.com" in _paths[0])
check("find_user_id 別名同一函式",
      d3.find_user_id("bob@corp.com") == "bob")
d3.assign("P-1", "bob")
check("assign dc:body 用 name 欄位", _bodies[-1] == {"name": "bob"})
check("my_uid dc:讀 name 非 accountId", d3.my_uid() == "bot1")
t = d3._to_ticket({"id": "7", "key": "P-7", "fields": {
    "assignee": {"displayName": "Bob", "name": "bob"}}})
check("_to_ticket dc:assignee_id=name", t.assignee_id == "bob")

print(f"test-jira-dc: {'PASS' if fail == 0 else 'FAIL'} ({ok}/{ok+fail})")
sys.exit(1 if fail else 0)
