import logging
import os
import shutil
import sys
from datetime import timedelta
from pathlib import Path

from aexpy.models import Release
from aexpy.tools.workers import AexPyDockerWorker, AexPyWorker

from . import env, initializeLogging
from .processor import DistPathBuilder, ProcessDB, Processor
from .std import StdProcessor

if __name__ == "__main__":
    initializeLogging(logging.INFO)

    command = "process"
    path = Path("./config.json")

    match sys.argv:
        case [_, cmd]:
            command = cmd
        case [_, cmd, cpath]:
            command = cmd
            path = Path(cpath)

    conf = env.load(path)
    env.prepare()

    if conf.db is None:
        conf.db = env.dist / "indexer.json"

    db = ProcessDB.load(conf.db)
    db.name = "aexpy-index"
    db.processLimit = 1000
    worker = (AexPyDockerWorker if conf.worker == "image" else AexPyWorker)(
        cwd=env.cache, verbose=5, compress=env.compress, logger=env.logger
    )

    env.logger.info(f"Current AexPy version: {worker.version()}")

    processor = Processor(worker, db, DistPathBuilder(env.dist / "data"))

    # std = StdProcessor(processor.worker, processor.db, processor.dist)
    # std.version(Release(project="python", version="3.12"))

    match command:
        case "index":
            processor.indexPackages()
        case "clear-std":
            db.data = {k: v for k, v in db.data.items() if "python@" not in k}
        case _:
            processor.packages(*conf.packages, timeout=timedelta(hours=4.0))
    db.save()
