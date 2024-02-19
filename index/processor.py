from contextlib import contextmanager
import datetime
from functools import cached_property
import json
from pathlib import Path
import shutil

from .dist import DistPathBuilder
from .aexpyw import AexPyWorker
from aexpy.models import Release, ReleasePair
from pydantic import BaseModel
from enum import IntEnum
from . import env
from aexpy import utils


class ProcessState(IntEnum):
    SUCCESS = 1
    FAILURE = 2


class ProcessResult(BaseModel):
    version: str
    state: ProcessState
    time: datetime.datetime


class ProcessDB(BaseModel):
    path: Path
    data: dict[str, ProcessResult] = {}
    processLimit: int | None = None
    processCount: int = 0

    def __getitem__(self, job: str):
        return self.data.get(job)

    @contextmanager
    def do(self, job: str, version: str):
        processChange = 0
        try:
            res = self[job]
            if res is None:
                processChange = 1
                yield res
            self.done(job, version, ProcessState.SUCCESS)
        except Exception as ex:
            env.logger.error(f"failed to do job: {job}", exc_info=ex)
            self.done(job, version, ProcessState.FAILURE)
            raise
        finally:
            self.processCount += processChange
            if self.processLimit is not None:
                if self.processCount >= self.processLimit:
                    env.logger.info(f"Meet process limit {self.processLimit}")
                    self.save()
                    exit(0)

    def done(self, job: str, version: str, state: ProcessState):
        self.data[job] = ProcessResult(
            version=version, state=state, time=datetime.datetime.now()
        )

    def save(self):
        self.path.write_text(self.model_dump_json())

    @classmethod
    def load(cls, file: Path):
        try:
            res = cls.model_validate_json(file.read_text())
            res.path = file
        except Exception as ex:
            env.logger.error(
                f"failed to load process db: {file}, use empty db", exc_info=ex
            )
            res = cls(path=file)
        return res


JOB_PREPROCESS = "preprocess"
JOB_EXTRACT = "extract"
JOB_DIFF = "diff"
JOB_REPORT = "report"


class Processor:
    def __init__(
        self, worker: AexPyWorker, db: ProcessDB, dist: DistPathBuilder
    ) -> None:
        self.worker = worker
        self.db = db
        self.dist = dist
        self.cacheDist = DistPathBuilder(env.cache)

    @cached_property
    def workerVersion(self):
        return self.worker.version()

    def hasDone(self, type: str, id: str):
        return f"{type}:{id}" in self.db.data

    def doOnce(self, type: str, id: str):
        if self.hasDone(type, id):
            return None
        @contextmanager
        def wrapper():
            with self.db.do(f"{type}:{id}", self.workerVersion):
                yield
        return wrapper

    def version(self, release: Release):
        env.logger.info(f"Process release {release}")
        dis = self.cacheDist.preprocess(release)
        api = self.cacheDist.extract(release)
        wheelDir = self.cacheDist.projectDir(release.project) / "wheels"
        utils.ensureDirectory(wheelDir)
        wrapper = self.doOnce(JOB_PREPROCESS, str(release))
        if wrapper:
            with wrapper():
                env.logger.info(f"Preprocess release {release}")
                result = self.worker.preprocess(
                    [
                        "-r",
                        "-p",
                        str(release),
                        str(self.worker.resolvePath(wheelDir)),
                        "-",
                    ]
                )
                result.save(dis)
                result.ensure().save(self.dist.preprocess(release))
        wrapper = self.doOnce(JOB_EXTRACT, str(release))
        if wrapper:
            with wrapper():
                env.logger.info(f"Extract release {release}")
                result = self.worker.extract([str(self.worker.resolvePath(dis)), "-"])
                result.save(api)
                result.ensure().save(self.dist.extract(release))
        shutil.rmtree(wheelDir, ignore_errors=True)

    def pair(self, pair: ReleasePair):
        env.logger.info(f"Process release pair {pair}")
        old = self.cacheDist.extract(pair.old)
        new = self.cacheDist.extract(pair.new)
        cha = self.cacheDist.diff(pair)
        rep = self.cacheDist.report(pair)
        
        wrapper = self.doOnce(JOB_DIFF, str(pair))
        if wrapper:
            with wrapper():
                env.logger.info(f"Diff releas pair {pair}")
                result = self.worker.diff(
                    [
                        str(self.worker.resolvePath(old)),
                        str(self.worker.resolvePath(new)),
                        "-",
                    ]
                )
                result.save(cha)
                result.ensure().save(self.dist.diff(pair))
        wrapper = self.doOnce(JOB_REPORT, str(pair))
        if wrapper:
            with wrapper():
                env.logger.info(f"Report releas pair {pair}")
                result = self.worker.report([str(self.worker.resolvePath(cha)), "-"])
                result.save(rep)
                result.ensure().save(self.dist.report(pair))

    def getReleases(self, project: str):
        from .release import single

        return single(project)[-40:]

    def package(self, project: str):
        from .release import pair

        env.logger.info(f"Process package {project}")

        releases = self.getReleases(project)
        env.logger.info(f"Find {len(releases)} releases: {releases}")

        doneReleases: list[Release] = []
        for rel in releases:
            env.logger.debug(f"Processing {rel}")
            try:
                self.version(rel)
                doneReleases.append(rel)
            except Exception as ex:
                env.logger.error(f"Failed to process {rel}", exc_info=ex)

        env.logger.info(
            f"Done {len(doneReleases)} / {len(releases)} releases: {doneReleases}"
        )

        pairs = pair(doneReleases)
        env.logger.info(f"Find {len(pairs)} release pairs: {pairs}")

        donePairs: list[ReleasePair] = []
        for rp in pairs:
            env.logger.debug(f"Processing {rp}")
            try:
                self.pair(rp)
                donePairs.append(rp)
            except Exception as ex:
                env.logger.error(f"Failed to process {rp}", exc_info=ex)

        self.index(project)

    def index(self, project: str):
        from .release import pair, sortedReleases

        env.logger.info(f"Index package {project}")

        releases = sortedReleases(self.getReleases(project))
        env.logger.info(f"Find {len(releases)} releases: {releases}")

        distributions = sortedReleases(self.dist.distributions(project))
        apis = sortedReleases(self.dist.apis(project))
        pairs = list(pair(apis))
        doneChanges = {str(x) for x in self.dist.changes(project)}
        doneReports = {str(x) for x in self.dist.reports(project)}
        changes = [x for x in pairs if str(x) in doneChanges]
        reports = [x for x in pairs if str(x) in doneReports]

        projectDir = self.dist.projectDir(project)
        utils.ensureDirectory(projectDir)
        (projectDir / "index.json").write_text(
            json.dumps(
                {
                    "releases": [str(r) for r in releases],
                    "distributions": [str(r) for r in distributions],
                    "apis": [str(r) for r in apis],
                    "pairs": [str(r) for r in pairs],
                    "changes": [str(r) for r in changes],
                    "reports": [str(r) for r in reports],
                }
            )
        )

    def packages(self, *projects: str, timeout: datetime.timedelta | None = None):
        doneProjects: list[str] = []
        with utils.elapsedTimer() as timer:
            for project in projects:
                if timeout and timer() > timeout:
                    env.logger.warning("Exceed timeout.")
                    break
                try:
                    if project != "python":
                        self.package(project)
                    else:
                        from .std import StdProcessor

                        std = StdProcessor(self.db, self.dist)
                        std.package(project)
                    doneProjects.append(project)
                except Exception as ex:
                    env.logger.error(f"Failed to process package: {project}", exc_info=ex)
    
    def indexPackages(self):
        doneProjects: list[str] = []
        for project in self.dist.projects():
            try:
                if project != "python":
                    self.index(project)
                else:
                    from .std import StdProcessor

                    std = StdProcessor(self.db, self.dist)
                    std.index(project)
                doneProjects.append(project)
            except Exception as ex:
                env.logger.error(f"Failed to index package: {project}", exc_info=ex)
        (env.dist / "packages.json").write_text(json.dumps(doneProjects))
