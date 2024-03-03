import gzip
import logging
import os
from pathlib import Path
from typing import override
from aexpy.extracting.environment import getExtractorEnvironmentBuilder
from aexpy.models import Release, ReleasePair, ApiDescription
from aexpy import utils

from index.dist import DistPathBuilder
from index.processor import ProcessDB

from .processor import (
    ProcessDB,
    Processor,
)
from .worker import AexPyResult, AexPyWorker
from . import env

IGNORED_MODULES = {"LICENSE", "site-packages"}


def getTopModules(path: Path):
    for p in path.glob("*"):
        if p.stem in IGNORED_MODULES:
            continue
        yield p.stem


def removeMain(path: Path):
    toRemove: list[Path] = []
    for item in path.glob("**/__main__.py"):
        if not item.is_file():
            continue
        if "site-packages" in item.parts:
            continue
        toRemove.append(item)
    env.logger.info(f"Remove __main__.py: {toRemove}")
    for item in toRemove:
        os.remove(item)


class StdProcessor(Processor):
    def __init__(
        self, worker: AexPyWorker, db: ProcessDB, dist: DistPathBuilder
    ) -> None:
        super().__init__(AexPyWorker(worker.compress), db, dist)
        self.envBuilder = getExtractorEnvironmentBuilder()

    @override
    def processVersion(self, release):
        env.logger.info(f"Process stdlib {str(release)}")

        envlogger = env.logger.getChild("std-env")
        envlogger.setLevel(logging.CRITICAL)
        with self.envBuilder.use(release.version, logger=envlogger) as e:
            with e as r:
                env.logger.info(f"Preprocess stdlib {str(release)}")
                dis = self.cacheDist.preprocess(release)
                pathRes = r.runPythonText(
                    '-c "import pathlib; print(pathlib.__file__)"', check=True
                )
                rootPath = Path(pathRes.stdout.strip()).parent
                # removeMain(rootPath)
                modules = list(getTopModules(rootPath))

                result = self.worker.preprocess(
                    [
                        "-s",
                        str(rootPath),
                        "-p",
                        str(release),
                        "-P",
                        release.version,
                        *sum([["-m", m] for m in modules], start=[]),
                        "-",
                    ]
                )
                result.save(dis)
                result.ensure().save(self.dist.preprocess(release))

                env.logger.info(f"Extract stdlib {str(release)}")
                api = self.cacheDist.extract(release)

                totalResult: ApiDescription | None = None
                totalLog = b""

                with utils.elapsedTimer() as timer:
                    for module in modules:
                        try:
                            result = self.worker.preprocess(
                                [
                                    "-s",
                                    str(rootPath),
                                    "-p",
                                    str(release),
                                    "-P",
                                    release.version,
                                    "-m",
                                    module,
                                    "-",
                                ],
                                check=True,
                            )
                            result = self.worker.extract(
                                ["-", "-", "-e", e.name], input=result.out
                            )
                        except Exception:
                            env.logger.error(
                                f"Failed to process std module {module} of {release}",
                                exc_info=True,
                            )
                            continue
                        totalLog += result.log

                        if result.data is None:
                            continue

                        if totalResult is None:
                            totalResult = result.data
                        else:
                            for entry in result.data:
                                if entry.id not in totalResult:
                                    totalResult.addEntry(entry)
                if totalResult is None:
                    finalResult = AexPyResult(code=1, log=b"Failed to dump", out=b"")
                else:
                    totalResult.distribution.topModules = modules
                    totalResult.duration = timer()
                    finalResult = AexPyResult(
                        out=totalResult.model_dump_json().encode(),
                        log=totalLog,
                        code=0,
                    )
                    if self.worker.compress:
                        finalResult.out = gzip.compress(finalResult.out)
                        finalResult.log = gzip.compress(finalResult.log)

                finalResult.save(api)
                finalResult.ensure().save(self.dist.extract(release))

    @override
    def processPair(self, pair):
        oldA = self.cacheDist.extract(pair.old)
        newA = self.cacheDist.extract(pair.new)
        if not oldA.is_file():
            oldA.write_bytes(self.dist.extract(pair.old).read_bytes())
        if not newA.is_file():
            newA.write_bytes(self.dist.extract(pair.new).read_bytes())

        env.logger.info(f"Diff stdlib {str(pair)}")
        cha = self.cacheDist.diff(pair)
        result = self.worker.diff([str(oldA), str(newA), "-"])
        result.save(cha)
        result.ensure().save(self.dist.diff(pair))

        env.logger.info(f"Report stdlib {str(pair)}")
        rep = self.cacheDist.report(pair)
        result = self.worker.report([str(cha), "-"])
        result.save(rep)
        result.ensure().save(self.dist.report(pair))

    @override
    def getReleases(self, project):
        return [Release(project="python", version=f"3.{x}") for x in range(8, 13)]
