"""Structured logging configuration (structlog, JSON output)."""

from __future__ import annotations

import logging
import sys
from typing import Any, TextIO

import structlog


def configure_logging(debug: bool = False, stream: TextIO | None = None) -> None:
    """Configure JSON logging.

    ``stream`` must be ``sys.stderr`` whenever stdout carries a protocol -- the
    stdio MCP transport exchanges JSON-RPC there and a single log line would
    corrupt the session.
    """
    target = stream or sys.stdout
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(format="%(message)s", stream=target, level=level, force=True)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(target),
        # Safe because stdio mode configures logging before anything can log.
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> Any:
    return structlog.get_logger(name)
