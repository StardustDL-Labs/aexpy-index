from contextlib import contextmanager
import logging
from pathlib import Path
import sys
from typing import override

LOGGING_FORMAT = "::%(levelname)s %(message)s"
LOG_INDENT = 2


def currentLogIndent():
    return LOGGING_FORMAT.index(":")


class LogFormatter(logging.Formatter):
    @override
    def formatException(self, ei):
        indent = currentLogIndent() + LOG_INDENT
        return "\n".join(
            " " * indent + s for s in super().formatException(ei).strip().splitlines()
        )


logHandler = logging.StreamHandler()


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
    originFormat = LOGGING_FORMAT
    LOGGING_FORMAT = f"{' '*LOG_INDENT}{LOGGING_FORMAT}"
    logHandler.setFormatter(logging.Formatter(LOGGING_FORMAT))
    indent = currentLogIndent()
    if title:
        print(f"{' '*indent}::group::{title}", file=sys.stderr)
    try:
        yield
    finally:
        LOGGING_FORMAT = originFormat
        logHandler.setFormatter(logging.Formatter(LOGGING_FORMAT))
        if title:
            print(f"{' '*indent}::endgroup::{title}", file=sys.stderr)


def getAppDirectory():
    return (Path(__file__).parent).resolve()
