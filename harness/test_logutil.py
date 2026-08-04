#!/usr/bin/env python3
"""W2.1 — logutil 單元測(pytest-compatible,亦可自跑)。"""
from __future__ import annotations

import logging
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arcp_harness import logutil  # noqa: E402


def test_level_from_env():
    os.environ["ARCP_LOG_LEVEL"] = "DEBUG"
    os.environ.pop("ARCP_LOG_FILE", None)
    logutil._configured_key = None
    log = logutil.get_logger("t1")
    assert log.getEffectiveLevel() == logging.DEBUG


def test_default_level_info():
    os.environ.pop("ARCP_LOG_LEVEL", None)
    os.environ.pop("ARCP_LOG_FILE", None)
    logutil._configured_key = None
    log = logutil.get_logger("t2")
    assert log.getEffectiveLevel() == logging.INFO


def test_file_handler_writes():
    logf = os.path.join(tempfile.mkdtemp(), "arcp.log")
    os.environ["ARCP_LOG_LEVEL"] = "INFO"
    os.environ["ARCP_LOG_FILE"] = logf
    logutil._configured_key = None
    log = logutil.get_logger("t3")
    log.info("hello-file-handler")
    for h in logging.getLogger("arcp").handlers:
        h.flush()
    assert os.path.exists(logf)
    assert "hello-file-handler" in open(logf).read()
    os.environ.pop("ARCP_LOG_FILE", None)


if __name__ == "__main__":
    ok = True
    for _name, _fn in list(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
                print(f"  PASS  {_name}")
            except AssertionError as e:
                ok = False
                print(f"  FAIL  {_name}: {e}")
    print("test-logutil:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
