"""C.0 gate spike — 極簡自製 AgentBase 子類(免 token,不 spawn CLI)。

只為驗證 UC1:agent-server 端能否反序列化並跑一個「我們自己的」agent 類。
step() 直接發一個 assistant MessageEvent 後把狀態設 finished —— 零成本。
真正的 RawCLIAgent(spawn claude -p + stream-json 解析)是 C.1/C.2。

被 server 子進程 import 觸發 DiscriminatedUnionMixin.__init_subclass__ 註冊,
resolve_kind("StubRawAgent") 才找得到(否則 "Unknown kind")。
"""

from __future__ import annotations

from openhands.sdk import Message, TextContent
from openhands.sdk.agent.base import AgentBase
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.llm import LLM
from pydantic import Field


def _dummy_llm() -> LLM:
    try:
        return LLM(model="c0-stub", usage_id="c0-stub")
    except Exception:
        return LLM(model="c0-stub")


class StubRawAgent(AgentBase):
    """Minimal custom agent to prove server-side instantiation of our own class."""

    llm: LLM = Field(default_factory=_dummy_llm)

    def init_state(self, state, on_event) -> None:  # noqa: ARG002
        return

    def step(self, conversation, on_event, on_token=None) -> None:  # noqa: ARG002
        on_event(MessageEvent(
            source="agent",
            llm_message=Message(role="assistant",
                                content=[TextContent(text="C0_STUB_OK")])))
        conversation.state.execution_status = \
            ConversationExecutionStatus.FINISHED
