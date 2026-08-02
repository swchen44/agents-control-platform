#!/usr/bin/env python3
"""C 前置 spike(時間盒:半天):SDK Agent 介面能否從外部實作,不 fork?

route C(RawCLIAgent)動工前唯一的未知數。四問四答:
  S1 AgentBase 契約:唯一抽象方法 step();llm 欄位可用 dummy(ACPAgent 前例)
  S2 Conversation(agent=<自製類>) 是否接受外部實作
  S3 事件路徑:step() 內 on_event(<SDK 事件>) 是否進 event 體系(callback 收到)
  S4 最小雛形:step() 直接 spawn `claude -p`(print mode),真 CLI 在 OpenHands
     Conversation 裡跑完一輪(檔案系統證據 + FINISHED)

Usage: .venv/bin/python spike_rawcli_agent.py    (~$0.02, haiku)
Artifacts: runtime_spike/
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

os.environ.setdefault("OPENHANDS_SUPPRESS_BANNER", "1")

from pydantic import Field  # noqa: E402

from openhands.sdk import Message, TextContent  # noqa: E402
from openhands.sdk.agent.base import AgentBase  # noqa: E402
from openhands.sdk.conversation import Conversation  # noqa: E402
from openhands.sdk.conversation.state import ConversationExecutionStatus  # noqa: E402
from openhands.sdk.event.llm_convertible import MessageEvent  # noqa: E402
from openhands.sdk.llm import LLM, content_to_str  # noqa: E402


def _dummy_llm() -> LLM:
    # same trick ACPAgent uses: the LLM field is mandatory but never called
    try:
        return LLM(model="rawcli-spike", usage_id="rawcli-spike")
    except Exception:
        return LLM(model="rawcli-spike")


class RawCLIAgentSpike(AgentBase):
    """Minimal RawCLIAgent seed: the execution unit is `claude -p` print mode.

    Deliberately tiny — one blocking CLI call per step, final text becomes an
    assistant MessageEvent. The real route-C agent would stream stream-json
    into fine-grained events here instead (all parsing knowledge already
    measured in examples/jira-agent-poc/arcp_poc/drivers.py).
    """

    llm: LLM = Field(default_factory=_dummy_llm)

    def init_state(self, state, on_event) -> None:  # noqa: ARG002
        # No SDK tool resolution: the CLI brings its own tools.
        return

    def step(self, conversation, on_event, on_token=None) -> None:  # noqa: ARG002
        state = conversation.state
        prompt = "Reply with exactly: pong"
        for ev in reversed(list(state.events)):
            if isinstance(ev, MessageEvent) and ev.source == "user":
                prompt = " ".join(content_to_str(ev.llm_message.content))
                break
        wd = getattr(getattr(state, "workspace", None), "working_dir", None) \
            or os.getcwd()
        proc = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "json",
             "--model", "haiku", "--permission-mode", "acceptEdits",
             "--allowedTools", "Write"],
            cwd=wd, capture_output=True, text=True, timeout=180,
            stdin=subprocess.DEVNULL)
        try:
            text = json.loads(proc.stdout).get("result") or "(empty result)"
        except (json.JSONDecodeError, AttributeError):
            text = f"(cli rc={proc.returncode}) {proc.stderr[:200]}"
        on_event(MessageEvent(
            source="agent",
            llm_message=Message(role="assistant",
                                content=[TextContent(text=text)])))
        state.execution_status = ConversationExecutionStatus.FINISHED


def main() -> int:
    root = os.path.abspath("./runtime_spike")
    shutil.rmtree(root, ignore_errors=True)
    ws = os.path.join(root, "ws")
    os.makedirs(ws)
    captured: list = []

    agent = RawCLIAgentSpike()
    conv = Conversation(agent=agent, workspace=ws,
                        callbacks=[captured.append])
    conv.send_message("用 Write 工具建立 spike_pong.txt,內容是字串 pong。"
                      "完成後回覆一行 DONE_SPIKE")
    conv.run()

    probe = os.path.join(ws, "spike_pong.txt")
    agent_msgs = [e for e in captured
                  if isinstance(e, MessageEvent) and e.source == "agent"]
    checks = {
        "S1_S2_custom_agent_accepted": True,  # reaching here = constructor+run ok
        "S3_events_reach_callbacks": bool(agent_msgs) and any(
            "DONE_SPIKE" in " ".join(content_to_str(m.llm_message.content))
            for m in agent_msgs),
        "S4_real_cli_ran_in_workspace": os.path.isfile(probe)
        and open(probe).read().strip() == "pong",
        "S4_finished": conv.state.execution_status
        == ConversationExecutionStatus.FINISHED,
    }
    with open(os.path.join(root, "spike_result.json"), "w") as f:
        json.dump({k: bool(v) for k, v in checks.items()}, f, indent=2)
    for k, v in checks.items():
        print(("PASS " if v else "FAIL "), k)
    print(f"captured events: {len(captured)}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
