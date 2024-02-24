from contextlib import contextmanager
import logging
from pathlib import Path
from typing import override

LOGGING_FORMAT = "[%(levelname)s] %(message)s"


class LogFormatter(logging.Formatter):
    @override
    def formatException(self, ei):
        indent = LOGGING_FORMAT.index("%") + 4
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
def indentLogging():
    global LOGGING_FORMAT
    originFormat = LOGGING_FORMAT
    LOGGING_FORMAT = f"    {LOGGING_FORMAT}"
    logHandler.setFormatter(logging.Formatter(LOGGING_FORMAT))
    try:
        yield
    finally:
        LOGGING_FORMAT = originFormat
        logHandler.setFormatter(logging.Formatter(LOGGING_FORMAT))


def getAppDirectory():
    return (Path(__file__).parent).resolve()
