"""Structured logging setup."""

import logging
import sys

_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=_FORMAT,
        datefmt=_DATE_FORMAT,
        stream=sys.stdout,
    )
    # Keep third-party loggers from being too chatty.
    for noisy in ("httpx", "httpcore", "urllib3", "neo4j"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
