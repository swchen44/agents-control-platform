#!/usr/bin/env python3
"""E2 — 長跑/大 context crash→resume 整測(live,需 claude/codex CLI 登入)。

宣稱(crash-safe 生產宣稱的最後硬證據):
  大 context 任務被 killpg(模擬 crash / evict / stall watchdog)後,
  以同 session id native resume 能 (a) 保留 crash 前的 context
  (b) 不重做已完成的工作 (c) 完成剩餘步驟。

做法(每引擎一格):
  1. workspace 生成 facts.txt(--lines 行 filler,中間埋 SECRET_TOKEN=<rand>)
     ——大 context 素材,agent 必須真的讀進 context。
  2. phase 1:三步任務「找 token→寫 memo.txt→sleep 30→寫 final.txt(token 反轉)」,
     fault_kill_on_file=memo.txt → memo 一出現就 killpg(final 還沒寫=真中斷)。
  3. **刪掉 facts.txt** ——關鍵設計:resume 後 token 的唯一來源是 session context,
     重讀檔案這條路被封死。
  4. phase 2:resume=True 同 session id,只說「繼續完成剩餘步驟」。
  5. 驗:final.txt == token 反轉(context 傳承)、memo.txt mtime 不變(不重工)、
     phase 2 有 terminal 事件(completed)。

Usage:
  uv run python tests/it_e2_resume.py                 # claude+codex 兩格,~$0.03
  uv run python tests/it_e2_resume.py --engine claude # 單格
  uv run python tests/it_e2_resume.py --lines 5000    # 放大 context(深測)

深測(E2 原案 30 分鐘級):--lines 50000 並把 prompt 的 sleep 拉長即可,
成本與筆電防睡限制見 docs/developer-guide.md「E2 長跑 resume」節
(不用 caffeinate——使用者明令;長窗任務建議接電源+盯 pmset -g log)。
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import string
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))
from arcp.rawcli.agent import RawCLIAgent  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "runtime-test", "it_e2_resume")

ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS  {name}")
    else:
        fail += 1; print(f"  FAIL  {name}  {detail}")


def make_facts(ws: str, lines: int) -> str:
    token = "".join(random.choices(string.ascii_uppercase + string.digits, k=10))
    rows = [f"fact_{i}: {''.join(random.choices(string.ascii_lowercase, k=60))}"
            for i in range(lines)]
    rows.insert(lines // 2, f"SECRET_TOKEN={token}")
    with open(os.path.join(ws, "facts.txt"), "w") as f:
        f.write("\n".join(rows) + "\n")
    return token


P1 = ("讀取目前目錄的 facts.txt,找到 SECRET_TOKEN= 開頭那行的值,然後嚴格依序:\n"
      "1. 把 token 值(只有值本身,單行無空白)寫入 memo.txt\n"
      "2. 執行 sleep 30\n"
      "3. 把 token 值的『字元反轉字串』寫入 final.txt(單行無空白)\n"
      "全部完成後回覆 E2_DONE。")
P2 = ("繼續完成先前任務剩下的步驟(從中斷處接續,已完成的步驟不要重做;"
      "sleep 那一步視為已完成,不要再等待、不要用背景任務)。"
      "用 Write 工具直接把 token 的字元反轉字串寫入 final.txt(相對路徑)。"
      "注意:facts.txt 已被移除,不要嘗試重建或重讀它,"
      "直接使用你先前已讀到的資訊。完成後回覆 E2_DONE。")


def run_case(engine: str, lines: int) -> None:
    print(f"=== E2 {engine}(facts {lines} 行)===")
    case = os.path.join(ROOT, engine)
    shutil.rmtree(case, ignore_errors=True)
    ws = os.path.join(case, "ws")
    os.makedirs(ws)
    token = make_facts(ws, lines)
    events = []

    def sink(e):
        events.append(e)

    memo = os.path.join(ws, "memo.txt")
    final = os.path.join(ws, "final.txt")
    model = "haiku" if engine == "claude" else None

    # phase 1:memo.txt 一出現 → killpg(模擬 crash)
    a1 = RawCLIAgent(engine=engine, model=model,
                     fault_kill_on_file="memo.txt", fault_delay=2.0,
                     raw_events_path=os.path.join(case, "p1.raw.jsonl"))
    t0 = time.time()
    a1.run(P1, ws, sink)
    check("p1: crash 於 sleep 窗(memo 有、final 無、無 terminal)",
          os.path.exists(memo) and not os.path.exists(final)
          and not a1._got_terminal,
          f"memo={os.path.exists(memo)} final={os.path.exists(final)} "
          f"term={a1._got_terminal}")
    check("p1: memo 內容 = token", open(memo).read().strip() == token
          if os.path.exists(memo) else False)
    sid = a1.session_id
    check("p1: crash 後仍拿得到 session id(resume 的鑰匙)", bool(sid),
          f"engine={engine}")

    memo_mtime = os.path.getmtime(memo)
    os.remove(os.path.join(ws, "facts.txt"))   # 封死重讀之路

    # phase 2:同 sid native resume
    # p2 只給 Write/Read:封掉 Bash,逼 agent 同步寫檔(claude 曾把
    # 「sleep && echo > final.txt」丟後台就收工,CLI 退出後台命令跟著死)
    a2 = RawCLIAgent(engine=engine, model=model, session_id=sid, resume=True,
                     stall_seconds=180, allowed_tools=["Write", "Read"],
                     raw_events_path=os.path.join(case, "p2.raw.jsonl"))
    a2.run(P2, ws, sink)
    check("p2: completed(terminal 事件)", a2._got_terminal)
    check("p2: context 傳承 —— final.txt = token 反轉(facts.txt 已刪,"
          "唯一來源是 session)",
          os.path.exists(final) and open(final).read().strip() == token[::-1],
          f"final={open(final).read().strip() if os.path.exists(final) else None}"
          f" expect={token[::-1]}")
    check("p2: 不重工 —— memo.txt 未被重寫(mtime 不變)",
          os.path.getmtime(memo) == memo_mtime)
    json.dump({"engine": engine, "lines": lines, "token": token,
               "sid": sid, "dur_s": round(time.time() - t0, 1),
               "events": len(events)},
              open(os.path.join(case, "result.json"), "w"), ensure_ascii=False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", choices=["claude", "codex", "both"],
                    default="both")
    ap.add_argument("--lines", type=int, default=600,
                    help="facts.txt 行數(600≈40KB;深測放大到 5000+)")
    args = ap.parse_args()
    engines = ["claude", "codex"] if args.engine == "both" else [args.engine]
    for e in engines:
        run_case(e, args.lines)
    print(f"it-e2-resume: {'PASS' if fail == 0 else 'FAIL'} ({ok}/{ok+fail})")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
