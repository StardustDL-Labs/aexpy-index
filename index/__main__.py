from datetime import timedelta
import os
import shutil
import sys
from pathlib import Path
import logging
from . import env, initializeLogging
from .worker import AexPyDockerWorker, AexPyWorker
from .processor import ProcessDB, Processor
from .dist import DistPathBuilder

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

    if (env.dist / "process.json").is_file():
        shutil.copytree(env.dist, env.dist.parent / "temp" / "data")
        shutil.rmtree(env.dist)
        shutil.move(env.dist.parent / "temp", env.dist)
        shutil.copyfile(env.dist / "data" / "process.json", env.dist / "indexer.json")
        os.remove(env.dist / "data" / "process.json")

    if conf.db is None:
        conf.db = env.dist / "indexer.json"
    exit(0)

    db = ProcessDB.load(conf.db)
    db.name = "aexpy-index"
    db.processLimit = 1000
    worker = (
        AexPyDockerWorker(env.compress)
        if conf.worker == "image"
        else AexPyWorker(env.compress)
    )

    env.logger.info(f"Current AexPy version: {worker.version()}")

    processor = Processor(worker, db, DistPathBuilder(env.dist))

    match command:
        case "index":
            processor.indexPackages()
        case "clear-std":
            db.data = {k: v for k, v in db.data.items() if "python@" not in k}
        case _:
            processor.packages(*conf.packages, timeout=timedelta(hours=4.0))
    db.save()
