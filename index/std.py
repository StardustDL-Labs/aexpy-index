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
    JOB_DIFF,
    JOB_EXTRACT,
    JOB_PREPROCESS,
    JOB_REPORT,
    ProcessResult,
    ProcessState,
    Processor,
)
from .worker import AexPyResult, AexPyWorker
from . import env

IGNORED_MODULES = {"LICENSE"}


def getTopModules(path: Path):
    for p in path.glob("*"):
        if p.stem.startswith("_") or "-" in p.stem or p.stem in IGNORED_MODULES:
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
    def __init__(self, db: ProcessDB, dist: DistPathBuilder) -> None:
        super().__init__(AexPyWorker(), db, dist)
        self.envBuilder = getExtractorEnvironmentBuilder()

    @override
    def version(self, release):
        wrapper = self.doOnce(JOB_EXTRACT, str(release))
        if isinstance(wrapper, ProcessResult):
            env.logger.info(f"Processed release {str(release)}")
            assert wrapper.state == ProcessState.SUCCESS, "not success"
            return

        env.logger.info(f"Process release {str(release)}")

        dis = self.cacheDist.preprocess(release)
        api = self.cacheDist.extract(release)

        envlogger = env.logger.getChild("std-env")
        envlogger.setLevel(logging.CRITICAL)
        with self.envBuilder.use(release.version, logger=envlogger) as e:
            with e as r:
                with wrapper():
                    pathRes = r.runPythonText(
                        '-c "import pathlib; print(pathlib.__file__)"', check=True
                    )
                    rootPath = Path(pathRes.stdout.strip()).parent
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

                    removeMain(rootPath)

                    totalResult: ApiDescription | None = None
                    totalLog = ""

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
                        finalResult = AexPyResult(code=1, log="Failed to dump", out="")
                    else:
                        totalResult.distribution.topModules = modules
                        totalResult.duration = timer()
                        finalResult = AexPyResult(
                            out=totalResult.model_dump_json(), log=totalLog, code=0
                        )

                    finalResult.save(api)
                    finalResult.ensure().save(self.dist.extract(release))

    @override
    def pair(self, pair):
        wrapper = self.doOnce(JOB_DIFF, str(pair))
        if isinstance(wrapper, ProcessResult):
            env.logger.info(f"Processed pair {str(pair)}")
            assert wrapper.state == ProcessState.SUCCESS, "not success"
            return

        env.logger.info(f"Process pair {str(pair)}")

        oldA = self.cacheDist.extract(pair.old)
        newA = self.cacheDist.extract(pair.new)

        if not oldA.is_file():
            oldA.write_bytes(self.dist.extract(pair.old).read_bytes())
        if not newA.is_file():
            newA.write_bytes(self.dist.extract(pair.new).read_bytes())

        cha = self.cacheDist.diff(pair)
        rep = self.cacheDist.report(pair)
        with wrapper():
            result = self.worker.diff([str(oldA), str(newA), "-"])
            result.save(cha)
            result.ensure().save(self.dist.diff(pair))

            result = self.worker.report([str(cha), "-"])
            result.save(rep)
            result.ensure().save(self.dist.report(pair))

    @override
    def getReleases(self, project):
        return [Release(project="python", version=f"3.{x}") for x in range(8, 13)]
