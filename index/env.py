import logging
from typing import Literal

from pydantic import BaseModel
from . import getAppDirectory
from pathlib import Path
from aexpy import utils

mirror = False
compress = False
cache = getAppDirectory() / "cache"
dist = getAppDirectory() / "dist"
logger = logging.getLogger()


def prepare():
    utils.ensureDirectory(cache)
    utils.ensureDirectory(dist)


class Config(BaseModel):
    cache: Path | None = None
    dist: Path | None = None
    mirror: bool | None = None
    db: Path | None = None
    worker: Literal["image"] | Literal["package"] = "package"
    packages: list[str] = []
    compress: bool | None = None


def load(configFile: Path):
    global mirror, cache, dist, compress
    conf = Config.model_validate_json(configFile.read_text())
    if conf.cache is not None:
        cache = conf.cache.resolve()
    if conf.dist is not None:
        dist = conf.dist.resolve()
    if conf.mirror is not None:
        mirror = conf.mirror
    if conf.compress is not None:
        compress = conf.compress
    return conf
