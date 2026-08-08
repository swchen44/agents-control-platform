"""Logging setup (W15) — 運維 debug 用,與 journal(events.jsonl 稽核)並存。

journal 是結構化稽核(每個決策一筆,給 detail page/KPI);logging 是運維日誌
(分級、給人看、未來 debug)。兩者職責不同,並存。

level 讀 `ARCP_LOG_LEVEL`(預設 INFO);設 `ARCP_LOG_FILE` 則同時寫檔。stdlib only。
"""

from __future__ import annotations

import logging
import os
import sys

_ROOT = "arcp"
_configured_key: tuple | None = None


def _configure() -> None:
    """Idempotent per (level, file) — re-runs if the env changed (tests)."""
    global _configured_key
    level = os.environ.get("ARCP_LOG_LEVEL", "INFO").upper()
    log_file = os.environ.get("ARCP_LOG_FILE") or None
    key = (level, log_file)
    if key == _configured_key:
        return
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-5s %(name)s: %(message)s", datefmt="%H:%M:%S")
    root = logging.getLogger(_ROOT)
    root.setLevel(getattr(logging, level, logging.INFO))
    root.handlers.clear()
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    if log_file:
        fh = logging.FileHandler(log_file)
        fh.setFormatter(fmt)
        root.addHandler(fh)
    root.propagate = False
    _configured_key = key


def get_logger(name: str) -> logging.Logger:
    """Return the `arcp.<name>` logger, configuring the root on first use."""
    _configure()
    return logging.getLogger(f"{_ROOT}.{name}")
