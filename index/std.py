import os
from pathlib import Path
from typing import override
from aexpy.environments.conda import CondaEnvironment, CondaEnvironmentBuilder
from aexpy.extracting.environment import getExtractorEnvironmentBuilder
from aexpy.models import Release, ReleasePair, ApiDescription

from index.dist import DistPathBuilder
from index.processor import ProcessDB

from .processor import (
    ProcessDB,
    JOB_DIFF,
    JOB_EXTRACT,
    JOB_PREPROCESS,
    JOB_REPORT,
    Processor,
)
from .aexpyw import AexPyResult, AexPyWorker
from . import env

IGNORED_MODULES = {"antigravity"}
CONSIDERED_MODULES = ["sys", "os", "pathlib", "math"]


def getTopModules(path: Path):
    # return CONSIDERED_MODULES
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
        need = not self.hasDone(JOB_EXTRACT, str(release))
        if not need:
            return

        dis = self.cacheDist.preprocess(release)
        api = self.cacheDist.extract(release)

        with self.envBuilder.use(release.version, logger=env.logger) as e:
            with e as r:
                with self.doOnce(JOB_EXTRACT, str(release)) as _:
                    if _ is None:
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
                        result.save(self.dist.preprocess(release))
                        result.ensure()

                        removeMain(rootPath)

                        totalResult: ApiDescription | None = None
                        totalLog = ""

                        for module in modules:
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
                            totalLog += result.log

                            if result.data is None:
                                continue

                            if totalResult is None:
                                totalResult = result.data
                            else:
                                for entry in result.data.entries.values():
                                    totalResult.addEntry(entry)
                        if totalResult is None:
                            finalResult = AexPyResult(
                                code=1, log="Failed to dump", out=""
                            )
                        else:
                            finalResult = AexPyResult(
                                out=totalResult.model_dump_json(), log=totalLog, code=0
                            )

                        finalResult.save(api)
                        finalResult.save(self.dist.extract(release))
                        finalResult.ensure()

    @override
    def pair(self, pair):
        need = not self.hasDone(JOB_DIFF, str(pair)) or not self.hasDone(
            JOB_REPORT, str(pair)
        )
        if not need:
            return

        oldA = self.cacheDist.extract(pair.old)
        newA = self.cacheDist.extract(pair.new)
        cha = self.cacheDist.diff(pair)
        rep = self.cacheDist.report(pair)
        with self.doOnce(JOB_DIFF, str(pair)) as _:
            if _ is None:
                result = self.worker.diff([str(oldA), str(newA), "-"])
                result.save(cha)
                result.save(self.dist.diff(pair))
                result.ensure()
        with self.doOnce(JOB_REPORT, str(pair)) as _:
            if _ is None:
                result = self.worker.report([str(cha), "-"])
                result.save(rep)
                result.save(self.dist.report(pair))
                result.ensure()

    @override
    def getReleases(self, project):
        return [Release(project="python", version=f"3.{x}") for x in range(12, 13)]
