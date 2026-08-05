"""Priority constants for plugin transformer ordering.

Built-in factory detectors run in a fixed sequence; these constants
expose their notional positions on a numeric priority scale so plugin
transformers can declare where they sit relative to the built-ins
without renumbering on every core change. Gaps of 100 leave room for
plugin insertion.

Convention (lower number = higher priority = runs first):

- ``0`` through ``999``  — user/system entry classification
- ``1000``               — generic text fallback (UserTextMessage)
- ``5000`` through ``9999`` — tool input / output classification
- ``10000`` and up       — never used by built-ins; reserved for plugin fallbacks

See ``work/tool-renderer-plugins.md`` §Priority + ordering for the
RFC discussion and worked examples.
"""

# User-entry detector chain (see factories/user_factory.py::create_user_message)
COMMAND_MESSAGE: int = 100
LOCAL_COMMAND_OUTPUT: int = 200
BASH_INPUT_OUTPUT: int = 300
TEAMMATE_MESSAGE: int = 400
TASK_NOTIFICATION: int = 500
HOOK_NOTIFICATION: int = 600  # PR #167's seat
SLASH_COMMAND_ISMETA: int = 700
TEXT_FALLBACK: int = 1000  # generic UserTextMessage

# Tool-entry classification
TOOL_INPUT_GENERIC: int = 5000
TOOL_OUTPUT_GENERIC: int = 5100


__all__ = [
    "BASH_INPUT_OUTPUT",
    "COMMAND_MESSAGE",
    "HOOK_NOTIFICATION",
    "LOCAL_COMMAND_OUTPUT",
    "SLASH_COMMAND_ISMETA",
    "TASK_NOTIFICATION",
    "TEAMMATE_MESSAGE",
    "TEXT_FALLBACK",
    "TOOL_INPUT_GENERIC",
    "TOOL_OUTPUT_GENERIC",
]
