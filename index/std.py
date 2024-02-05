import os
from pathlib import Path
from typing import override
from aexpy.environments.conda import CondaEnvironment, CondaEnvironmentBuilder
from aexpy.extracting.environment import getExtractorEnvironmentBuilder
from aexpy.models import Release, ReleasePair

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
from .aexpyw import AexPyWorker
from . import env

IGNORED_MODULES = {"antigravity"}


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
        if "pip" in path.parts:
            continue
        toRemove.append(item)
    for item in toRemove:
        os.remove(item)


class StdProcessor(Processor):
    def __init__(self, db: ProcessDB, dist: DistPathBuilder) -> None:
        super().__init__(AexPyWorker(), db, dist)
        self.envBuilder = getExtractorEnvironmentBuilder()

    @override
    def version(self, release):
        need = not self.hasDone(JOB_PREPROCESS, str(release)) or not self.hasDone(
            JOB_EXTRACT, str(release)
        )
        if not need:
            return

        dis = self.cacheDist.preprocess(release)
        api = self.cacheDist.extract(release)

        with self.envBuilder.use(release.version, logger=env.logger) as e:
            with e as r:
                pathRes = r.runPythonText(
                    '-c "import pathlib; print(pathlib.__file__)"', check=True
                )
                rootPath = Path(pathRes.stdout.strip()).parent
                modules = sum([["-m", s] for s in getTopModules(rootPath)], start=[])
                with self.doOnce(JOB_PREPROCESS, str(release)) as _:
                    if _ is None:
                        result = self.worker.preprocess(
                            [
                                "-s",
                                str(rootPath),
                                "-p",
                                str(release),
                                "-P",
                                release.version,
                                *modules,
                                "-",
                            ]
                        )
                        result.save(dis)
                        result.save(self.dist.preprocess(release))
                        result.ensure()
                removeMain(rootPath)
                with self.doOnce(JOB_EXTRACT, str(release)) as _:
                    if _ is None:
                        result = self.worker.extract([str(dis), "-", "-e", e.name])
                        result.save(api)
                        result.save(self.dist.extract(release))
                        result.ensure()

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
