#!/usr/bin/env python3
"""V1 複驗助手 —— 把「真環境複驗」的免費部分自動化,並印出付費(真派工)部分的引導清單。

背景:離線 CI 蓋不到「真 agent 派工」那條路。這支把**不花錢**的檢查一次跑完:
  1. runner 路徑解析(W12.1 曾壞的 bug)      —— 純本機
  2. config.yaml / config.example.yaml 載入   —— 純本機(抓設定錯)
  3. profiles 載入 + select 設定驗證          —— 純本機
  4. journal 事件字典未漂移                    —— 純本機
  5. Jira 連線(myself + search,唯讀)        —— 需 ~/.env,免費不派 agent

然後印出**付費部分**(真派一次工)的手動清單:要開什麼票、在 journal/dashboard 看哪些
事件,確認 W15 install / Q16 select / Q11 hold / Q13 自評 / Q10 human-prompt 這些新路徑
在真 agent 下如預期。

用法:
  uv run python scripts/reverify_v1.py            # 全跑(含 Jira 唯讀;無 ~/.env 則跳過該項)
  uv run python scripts/reverify_v1.py --offline  # 只跑不需憑證的本機檢查
"""
from __future__ import annotations

import os
import sys

from arcp.paths import config_dir, config_path

_ok = _fail = 0


def _check(name: str, cond: bool, detail: str = "") -> None:
    global _ok, _fail
    mark = "✓" if cond else "✗"
    if cond:
        _ok += 1
    else:
        _fail += 1
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))


def offline_checks() -> None:
    print("== 免費本機檢查 ==")
    # 1. runner 路徑(W12.1 bug)
    try:
        from arcp.inner_runner import RUNNERS
        miss = [k for k, v in RUNNERS.items() if not os.path.exists(v)]
        _check("runner 路徑解析(rawcli/openhands)", not miss,
               "缺:" + ", ".join(miss) if miss else "3/3 皆在 scripts/")
    except Exception as e:  # noqa: BLE001
        _check("runner 路徑解析", False, repr(e))

    # 2+3. config + profiles + select 驗證
    from arcp.profiles import load_profiles
    from arcp.routing import load_config
    for label, fn in (("config.yaml", config_path()),
                      ("config.example.yaml",
                       os.path.join(config_dir() or ".", "config.example.yaml"))):
        try:
            load_config(fn)
            profs = load_profiles(fn)
            sel = [n for n, p in profs.items() if p.select]
            _check(f"{label} 載入 + {len(profs)} profiles"
                   + (f"(select:{sel})" if sel else ""), True)
        except Exception as e:  # noqa: BLE001
            _check(f"{label} 載入", False, repr(e))

    # 4. 事件字典未漂移
    import subprocess
    r = subprocess.run([sys.executable, os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "gen_event_dict.py"),
        "--check"], capture_output=True, text=True)
    _check("journal 事件字典未漂移", r.returncode == 0,
           (r.stdout + r.stderr).strip().splitlines()[-1] if r.stdout or r.stderr
           else "")


def jira_read_check() -> None:
    print("== Jira 唯讀連線(免費,需 ~/.env)==")
    try:
        from arcp.config import jira_credentials
        from arcp.jira_source import JiraCloudSource
    except Exception as e:  # noqa: BLE001
        _check("import jira_source", False, repr(e)); return
    try:
        src = JiraCloudSource(*jira_credentials())
    except Exception as e:  # noqa: BLE001
        _check("讀 ~/.env 憑證", False, f"{e}(無憑證 → 跳過 Jira 檢查)"); return
    try:
        me = src.myself()
        _check("Jira auth(myself)", bool(me.get("accountId")),
               "accountId 已取得(不顯示值)")
    except Exception as e:  # noqa: BLE001
        _check("Jira auth(myself)", False, repr(e)); return
    try:
        from arcp.routing import load_config
        jql = (load_config(config_path())[0] or {}).get("jql", "")
        n = len(src.search(jql)) if jql else 0
        _check("Jira search(config jql,唯讀)", True, f"撈到 {n} 張票")
    except Exception as e:  # noqa: BLE001
        _check("Jira search", False, repr(e))


_CHECKLIST = """
== 付費部分:真派一次工才驗得到(需你在充電 + 願花 ~$0.03–0.05 haiku)==
建議用一個便宜(model=haiku)、帶 select 的測試 profile 跑一張測試票,然後看
dashboard ticket 頁 trace + runtime/events.jsonl,逐項確認新路徑:

  [ ] runner spawn:出現 session_created → attempt_started → attempt_finished
      (raw=completed/error,有 aN.envelope.json)   ← W12.1 runner-path + envelope
  [ ] Q16 select:若 main profile 設了 select → journal 有 profile_selected
      (original/chosen);chosen 就是實際跑的 profile
  [ ] W15 install:若該 profile 用 workspace_install → workspace 有 install 產物 +
      logger 有 [install] 輸出;中途 kill 再跑不會用半殘 ws(.arcp_provisioned marker)
  [ ] Q11 hold:票上留言 @agent hold → 立即 evict(journal evicted)+ 開 hold 表單;
      填表(給新 prompt)→ journal hil_resumed + TICKET.md 出現「人類指示」段 → 續跑
  [ ] Q10 human prompt:HIL 表單填 human_prompt → runtime .../ws/.arcp_human.md 有該行,
      且下輪 TICKET.md 含它
  [ ] Q13 自評:成功後 score_and_close 表單顯示 agent 自評(需 run_poller 接上
      self_score_fn;預設 None 則顯示 —)
  [ ] C3/C5 retry 計數 flaky:觀察 retry 情境的 attempt 計數是否穩定(memory:
      e2e-commands-c3-c5-flaky)
  [ ] W10.3 handoff(HIL 表單):終態發 score_and_close → 選 close_decision=handoff。
      · next:handoff_kind=next + next_profile → journal handoff(kind=next,via=hil);
        下輪換該 profile 跑同票、TICKET.md 描述含交接指示
      · base:handoff_kind=base → 系統 create_ticket 建新票(同 project、summary 帶
        [base:<key>])、本票轉 ABORTED;新票下輪 journal base_injected + ws 有
        BASE_<key>/(含來源 TICKET.md/envelope)+ 人類指示段指路。⚠️ 這會真的建一張
        新 Jira 票(唯一在 handoff 路徑的寫入),請在測試 project 上驗

看不到某項時:先 scripts/trace_lint.py 檢查該票四層證據齊不齊,再對照
docs/design/observability.md 的事件字典 + docs/troubleshooting.md。
"""


def main() -> int:
    offline_checks()
    if "--offline" not in sys.argv:
        jira_read_check()
    print(f"\n免費檢查:{_ok} 過 / {_fail} 失敗")
    print(_CHECKLIST)
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
