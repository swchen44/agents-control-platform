"""RawCLIAgent — route C: OpenHands 骨架 + raw CLI 執行單元(不走 ACP)。

在 OpenHands SDK 內以自製 AgentBase 子類直接 spawn `claude -p`/`codex exec`、
解析原生 stream-json、發細粒度事件進 event-sourced 體系。事件回到 A 級,
無 ACP 的 14-vs-248 粒度損失、無 adapter 版本鏈、有中途控制窗口。

Importing this package registers RawCLIAgent for server-side resolve_kind()
(C.0 gate:server 啟動時 import 觸發 __init_subclass__ 註冊)。
"""

from .agent import RawCLIAgent

__all__ = ["RawCLIAgent"]
