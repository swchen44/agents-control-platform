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

# ── L5:dc 純文字/wiki(comment/description/deliverables)──────────── #
d3._request = _fake                    # 重掛 spy(前面測試已換過)
_bodies.clear()
d3.add_comment("P-1", "哈囉")
check("add_comment dc:body 是純文字字串",
      _bodies[-1] == {"body": "哈囉"})
d3.set_description("P-1", "內容")
check("set_description dc:字串(cloud 才 ADF)",
      _bodies[-1] == {"fields": {"description": "內容"}})
_cb = []
c._request = lambda m, p, params=None, body=None: _cb.append(body) or {}
c.add_comment("P-1", "hi")
check("add_comment cloud:仍是 ADF dict(零變)",
      isinstance(_cb[-1]["body"], dict) and _cb[-1]["body"]["type"] == "doc")

# Cloud ADF mention:[~accountid:ID] 必須拆成 mention node 才會通知(非死文字)
from arcp.jira_source import text_to_adf  # noqa: E402

_adf = text_to_adf("[agent] [~accountid:abc-123] 需要你:填表")
_nodes = _adf["content"][0]["content"]
check("text_to_adf:[~accountid:] → mention node(渲染+通知)",
      any(n["type"] == "mention" and n["attrs"]["id"] == "abc-123"
          for n in _nodes)
      and _nodes[0] == {"type": "text", "text": "[agent] "})
check("text_to_adf:無 mention 的行仍是純 text node",
      text_to_adf("純文字")["content"][0]["content"]
      == [{"type": "text", "text": "純文字"}])

import json as _json  # noqa: E402
import tempfile as _tf  # noqa: E402

from arcp.deliverables import build_comment_wiki, post_deliverables  # noqa: E402
from arcp.output import load_output  # noqa: E402
from arcp.store import Store, TicketSession  # noqa: E402
from arcp.ticket import Ticket  # noqa: E402

_wsd = _tf.mkdtemp()
_json.dump({"summary_md": "n", "code": [{"url": "http://g/+/1",
                                         "note": "改了X"}],
            "references": [{"label": "R", "path_or_url": "http://r"}]},
           open(os.path.join(_wsd, "OUTPUT.json"), "w"))
_out = load_output(_wsd)
wiki = build_comment_wiki(outcome="SUCCESS", attempt=2, cost_usd=0.5,
                          self_summary="完成A", output=_out,
                          attach_names=[], mode="none", download_url=None,
                          base_url=None, key="P-1")
check("build_comment_wiki:h3/h4/連結/自報 wiki 語法",
      wiki.startswith("h3. [agent] outcome=SUCCESS")
      and "*自報:* 完成A" in wiki and "* [改了X|http://g/+/1]" in wiki
      and "h4. 程式碼(Gerrit)" in wiki and "* [R|http://r]" in wiki)


class _DCSrc:                                   # dc source:交付物走 wiki 路
    flavor = "dc"

    def __init__(self):
        self.comments, self.adf = [], []

    def add_comment(self, iid, text):
        self.comments.append(text)

    def add_comment_adf(self, iid, doc, detail=""):
        self.adf.append(doc)


_st = Store(_tf.mkdtemp())
_sess = TicketSession(issue_id=1, key="P-1", profile="p", workspace=_wsd,
                      session_id="s", attempts=1, outcome="SUCCESS",
                      pending_reason=None, cost_usd=0.1)
_tk = Ticket(id=1, key="P-1", summary="s", state="Done", assignee=None,
             assignee_id=None, description="")
_dc = _DCSrc()
post_deliverables(_dc, _st, _tk, _sess, outcome="SUCCESS",
                  self_summary="done")
check("post_deliverables dc:走 add_comment(wiki),不走 ADF",
      len(_dc.comments) == 1 and _dc.adf == []
      and _dc.comments[0].startswith("h3. "))

# ── L6/L7:resolve_user_id 查序(map → 快取 → search 寫回 → rule)──── #
from arcp.identity import resolve_user_id  # noqa: E402

_st2 = Store(_tf.mkdtemp())


class _Search:
    def __init__(self, ret):
        self.ret, self.calls = ret, 0

    def find_user_id(self, email):
        self.calls += 1
        return self.ret


check("resolve:user_map 最優先(不打 search)",
      resolve_user_id("A@corp.com", _Search("wrong"), _st2,
                      {"a@corp.com": "map-uid"}) == "map-uid")
s1 = _Search("hit-1")
check("resolve:search 命中 → 回傳 + 寫回快取",
      resolve_user_id("b@corp.com", s1, _st2) == "hit-1"
      and _st2.get_user_uid("b@corp.com") == "hit-1")
s2 = _Search("no-call")
check("resolve:第二次走快取(search 不再打)",
      resolve_user_id("b@corp.com", s2, _st2) == "hit-1" and s2.calls == 0)
check("resolve:全 miss + rule=local → email 前段",
      resolve_user_id("carol@corp.com", _Search(None), _st2,
                      username_rule="local") == "carol")
check("resolve:{local} 模板", resolve_user_id(
    "dave@corp.com", _Search(None), _st2,
    username_rule="ad-{local}") == "ad-dave")
check("resolve:全 miss 無 rule → None",
      resolve_user_id("eve@corp.com", _Search(None), _st2) is None)
check("resolve:空 email → None", resolve_user_id("", _Search("x")) is None)
_st2.close()

print(f"test-jira-dc: {'PASS' if fail == 0 else 'FAIL'} ({ok}/{ok+fail})")
sys.exit(1 if fail else 0)
