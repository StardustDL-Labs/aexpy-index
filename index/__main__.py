from datetime import timedelta
import sys
from pathlib import Path
import logging
from . import env, initializeLogging
from .worker import AexPyDockerWorker, AexPyWorker
from .processor import ProcessDB, Processor
from .dist import DistPathBuilder

if __name__ == "__main__":
    initializeLogging(logging.INFO)

    isOnlyIndex = False
    path = Path("./config.json")

    match sys.argv:
        case [_, command]:
            if command == "index":
                isOnlyIndex = True
        case [_, command, cpath]:
            if command == "index":
                isOnlyIndex = True

    conf = env.load(path)
    env.prepare()

    if conf.db is None:
        conf.db = env.dist / "process.json"

    db = ProcessDB.load(conf.db)
    db.processLimit = 500
    worker = AexPyDockerWorker() if conf.worker == "image" else AexPyWorker()

    env.logger.info(f"Current AexPy version: {worker.version()}")

    processor = Processor(worker, db, DistPathBuilder(env.dist))
    if isOnlyIndex:
        processor.indexPackages()
    else:
        processor.packages(*conf.packages, timeout=timedelta(hours=2.0))
    db.save()
