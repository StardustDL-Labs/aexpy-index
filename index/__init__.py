import os
from pathlib import Path


def getAppDirectory():
    return (Path(__file__).parent).resolve()
