#!/usr/bin/env python3
"""Phase 3 E2E — 指令核心端到端(真實 Jira,人機協作閉環)。

人的指令改走「指令台」表單 → apply_command(取代舊 @agent comment 通道)。本測試直接
呼 apply_command 驗證效果 + poller 重派:

  C1 cmddemo 票 → dispatch → 確定性 FAILURE(verify 必敗;hil_end 可下指令)
  C2 retry → attempts 歸零、下輪重派(journal 計 2 次 attempt)
  C3 cancel → ABORTED(abort_reason=cancel)
  C4 再 poll → 終態不再派工

2026-08-12 改版:舊路(faultdead 10s timeout→UNKNOWN)因 ACP 啟動快慢不定
而 flaky(暖機 haiku 10s 內跑完變 SUCCESS)——改 cmddemo profile(rawcli+
verify 必敗)確定性 FAILURE。測後自動把測試票轉 done(不留殘票)。

Usage: python3 e2e_commands.py  (live;兩次 haiku attempt,~$0.06)
"""

from __future__ import annotations

import json
import shutil
import sys
import time

from arcp.commands import ExternalChangePolicy, apply_command
from arcp.config import jira_credentials
from arcp.dispatcher import Dispatcher
from arcp.jira_source import JiraCloudSource
from arcp.paths import config_path
from arcp.poller import OuterLoop
from arcp.profiles import load_profiles
from arcp.routing import load_config
from arcp.store import Store


def attempts_in_journal(path: str, issue_id: int) -> int:
    try:
        return sum(1 for l in open(path)
                   if (e := json.loads(l))["type"] == "attempt_finished"
                   and e["issue_id"] == issue_id)
    except FileNotFoundError:
        return 0


def main() -> int:
    src = JiraCloudSource(*jira_credentials())
    _, routes = load_config(config_path())
    profiles = load_profiles(config_path())
    shutil.rmtree("./runtime_cmd", ignore_errors=True)
    store = Store("./runtime_cmd")
    # J3:label 已全面 arcp. 前綴(2026-08-12 複驗抓到此 jql 漏改 → 票撈不到全鏈 FAIL)
    # created >= -5m:只撈本輪新票(歷史殘票會被重派、污染計數+多花錢)
    jql = ('project = SCRUM AND labels = arcp.cmddemo'
           ' AND statusCategory != Done AND created >= "-5m"')
    loop = OuterLoop(
        src, store, routes, jql,
        dispatcher=Dispatcher(src, store, profiles, root="./runtime_cmd"),
        external=ExternalChangePolicy(src, store, ["完成", "Done"]))

    t = src.create_ticket("SCRUM", f"[e2e-cmd] 指令核心 {int(time.time())}",
                          description="測試指令核心(會 timeout 進 pending)",
                          labels=["arcp.cmddemo"])
    print(f"ticket: #{t.id} {t.key}", flush=True)
    journal = "./runtime_cmd/events.jsonl"
    by = "tester@example.com"

    loop.poll_once()
    s = store.get_session(t.id)
    # cmddemo profile:verify 必敗 → 確定性 FAILURE(hil_end,可下 retry/cancel)。
    # 舊路(faultdead timeout→UNKNOWN)因 ACP 啟動快慢不定而 flaky,已棄。
    c1 = s and s.outcome == "FAILURE"
    print(f"C1 dispatch→FAILURE(hil_end,可下指令): {'PASS' if c1 else 'FAIL'}")

    ok_r, msg_r, _ = apply_command(src, store, profiles, t.id, "retry", by=by)
    loop.poll_once()
    n_attempts = attempts_in_journal(journal, t.id)
    c2 = ok_r and n_attempts == 2
    print(f"C2 retry→重派(attempts_in_journal={n_attempts}): "
          f"{'PASS' if c2 else 'FAIL'}")

    ok_c, msg_c, _ = apply_command(src, store, profiles, t.id, "cancel", by=by)
    s = store.get_session(t.id)
    c3 = ok_c and s and s.outcome == "ABORTED"
    print(f"C3 cancel→ABORTED: {'PASS' if c3 else 'FAIL'}")

    loop.poll_once()
    c4 = attempts_in_journal(journal, t.id) == 2
    print(f"C4 ABORTED 後不再派工: {'PASS' if c4 else 'FAIL'}")

    try:                                # 收尾:測試票轉 done(不留 open 殘票)
        src.transition(t.id, "done")
    except Exception as e:              # noqa: BLE001
        print(f"(收尾 transition 失敗,請手關 {t.key}:{e})")
    store.close()
    ok = all([c1, c2, c3, c4])
    print("e2e-commands:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
