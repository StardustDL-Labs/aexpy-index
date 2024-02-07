from datetime import timedelta
import sys
from pathlib import Path
import logging
from . import env
from .aexpyw import AexPyDockerWorker, AexPyWorker
from .processor import ProcessDB, Processor
from .dist import DistPathBuilder

if __name__ == "__main__":
    logging.basicConfig(level=logging.NOTSET)

    if len(sys.argv) == 2:
        path = Path(sys.argv[1])
    else:
        path = Path("./config.json")

    conf = env.load(path)
    env.prepare()

    if conf.db is None:
        conf.db = env.dist / "process.json"

    db = ProcessDB.load(conf.db)
    if conf.worker == "image":
        worker = AexPyDockerWorker()
    else:
        worker = AexPyWorker()

    env.logger.info(f"Current AexPy version: {worker.version()}")

    processor = Processor(worker, db, DistPathBuilder(env.dist))
    processor.packages(*conf.packages, timeout=timedelta(hours=2.0))
    db.save()
