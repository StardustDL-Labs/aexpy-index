from contextlib import contextmanager
import logging
from pathlib import Path
import sys
from typing import override

LOGGING_FORMAT = "[%(levelname)s] %(message)s"
LOG_INDENT = 2

logHandler = logging.StreamHandler()


def currentLogIndent():
    return ((logHandler.formatter._fmt if logHandler.formatter else "") or "").index(
        "["
    )


class LogFormatter(logging.Formatter):
    @override
    def formatException(self, ei):
        indent = currentLogIndent() + LOG_INDENT
        return "\n".join(
            " " * indent + s for s in super().formatException(ei).strip().splitlines()
        )


def initializeLogging(level: int = logging.WARNING):
    root = logging.getLogger()
    root.setLevel(logging.NOTSET)
    root.handlers.clear()
    logHandler.setLevel(level)
    logHandler.setFormatter(LogFormatter(LOGGING_FORMAT))
    root.addHandler(logHandler)


@contextmanager
def indentLogging(title: str = ""):
    global LOGGING_FORMAT
    indent = currentLogIndent()
    originFormat = f"{' '*indent}{LOGGING_FORMAT}"
    logHandler.setFormatter(logging.Formatter(f"{' '*LOG_INDENT}{originFormat}"))
    if title:
        print(f"{' '*indent}::group::{' '*indent}{title}", file=sys.stderr)
    try:
        yield
    finally:
        logHandler.setFormatter(logging.Formatter(originFormat))
        if title:
            print(f"{' '*indent}::endgroup::", file=sys.stderr)


def getAppDirectory():
    return (Path(__file__).parent).resolve()
