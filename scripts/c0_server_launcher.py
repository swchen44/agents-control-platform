"""Launch agent-server with our custom agent module pre-imported (C.0 gate).

Importing c0_stub_agent triggers DiscriminatedUnionMixin.__init_subclass__,
registering StubRawAgent so the server's resolve_kind() can find it. This is
the general mechanism route C would use to run a custom RawCLIAgent on the
agent-server: control the server's launch → import the agent module first.

argv: c0_server_launcher.py <host> <port>
"""

import runpy
import sys

import c0_stub_agent  # noqa: F401  — registers StubRawAgent (the whole point)

host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
port = sys.argv[2] if len(sys.argv) > 2 else "18020"
sys.argv = ["agent_server", "--host", host, "--port", port]
runpy.run_module("openhands.agent_server", run_name="__main__")
