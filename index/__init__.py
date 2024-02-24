from contextlib import contextmanager
import logging
from pathlib import Path

LOGGING_FORMAT = "%(levelname)s %(message)s"

logHandler = logging.StreamHandler()


def initializeLogging(level: int = logging.WARNING):
    root = logging.getLogger()
    root.setLevel(logging.NOTSET)
    root.handlers.clear()
    logHandler.setLevel(level)
    logHandler.setFormatter(logging.Formatter(LOGGING_FORMAT))
    root.addHandler(logHandler)


@contextmanager
def indentLogging():
    global LOGGING_FORMAT
    originFormat = LOGGING_FORMAT
    LOGGING_FORMAT = f"  {LOGGING_FORMAT}"
    logHandler.setFormatter(logging.Formatter(LOGGING_FORMAT))
    try:
        yield
    finally:
        LOGGING_FORMAT = originFormat
        logHandler.setFormatter(logging.Formatter(LOGGING_FORMAT))


def getAppDirectory():
    return (Path(__file__).parent).resolve()
