"""RawCLIAgent — rawcli 執行單元:直接 spawn `claude -p`/`codex exec`。

W5.5 起**零 OpenHands 依賴**(純 stdlib):自建進程 + 解析原生 stream-json +
發細粒度事件(dict,與舊 SDK MessageEvent 同 JSONL 形狀,dashboard 零改)。
事件保真回到 A 級,無 ACP 粒度損失、無 adapter 版本鏈、有中途控制窗口。
(openhands-acp / openhands-server backend 仍走 SDK,需裝 openhands venv;
rawcli 主線不再需要。)
"""

from .agent import RawCLIAgent

__all__ = ["RawCLIAgent"]
