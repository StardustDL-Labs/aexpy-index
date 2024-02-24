from contextlib import contextmanager
import datetime
from functools import cached_property
import json
from pathlib import Path
import shutil

from .dist import DistPathBuilder
from .worker import AexPyWorker
from aexpy.models import Release, ReleasePair
from pydantic import BaseModel
from enum import IntEnum
from . import env, indentLogging
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
        try:
            yield
            self.done(job, version, ProcessState.SUCCESS)
        except Exception as ex:
            env.logger.error(f"failed to do job: {job}", exc_info=ex)
            self.done(job, version, ProcessState.FAILURE)
            raise
        finally:
            self.processCount += 1
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
        res.processCount = 0
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
        item = self.db[f"{type}:{id}"]
        return item and item.version == self.workerVersion

    def doOnce(self, type: str, id: str):# -> ProcessResult | Callable[[], _GeneratorContextManager[None]]:
        if self.hasDone(type, id):
            res = self.db[f"{type}:{id}"]
            assert res is not None
            return res

        @contextmanager
        def wrapper():
            with self.db.do(f"{type}:{id}", self.workerVersion):
                yield

        return wrapper

    def version(self, release: Release):
        dis = self.cacheDist.preprocess(release)
        api = self.cacheDist.extract(release)
        wheelDir = self.cacheDist.projectDir(release.project) / "wheels"
        utils.ensureDirectory(wheelDir)
        wrapper = self.doOnce(JOB_PREPROCESS, str(release))
        if isinstance(wrapper, ProcessResult):
            env.logger.info(f"Preprocessed {release=}")
            assert wrapper.state == ProcessState.SUCCESS, "not success"
        else:
            env.logger.info(f"Preprocess {release=}")
            with wrapper():
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
        if isinstance(wrapper, ProcessResult):
            env.logger.info(f"Extracted {release=}")
            assert wrapper.state == ProcessState.SUCCESS, "not success"
        else:
            env.logger.info(f"Extract {release=}")
            with wrapper():
                result = self.worker.extract([str(self.worker.resolvePath(dis)), "-"])
                result.save(api)
                result.ensure().save(self.dist.extract(release))
        shutil.rmtree(wheelDir, ignore_errors=True)

    def pair(self, pair: ReleasePair):
        old = self.cacheDist.extract(pair.old)
        new = self.cacheDist.extract(pair.new)
        cha = self.cacheDist.diff(pair)
        rep = self.cacheDist.report(pair)

        wrapper = self.doOnce(JOB_DIFF, str(pair))
        if isinstance(wrapper, ProcessResult):
            env.logger.info(f"Diffed {pair=}")
            assert wrapper.state == ProcessState.SUCCESS, "not success"
        else:
            env.logger.info(f"Diff {pair=}")
            with wrapper():
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
        if isinstance(wrapper, ProcessResult):
            env.logger.info(f"Reported {pair=}")
            assert wrapper.state == ProcessState.SUCCESS, "not success"
        else:
            env.logger.info(f"Report {pair=}")
            with wrapper():
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
        env.logger.info(f"Found {len(releases)} {releases=}")

        doneReleases: list[Release] = []
        for release in releases:
            with indentLogging():
                try:
                    self.version(release)
                    doneReleases.append(release)
                except Exception as ex:
                    env.logger.error(f"Failed to process {release=}", exc_info=ex)

        env.logger.info(
            f"Done {len(doneReleases)} / {len(releases)} releases: {doneReleases}"
        )

        pairs = pair(doneReleases)
        env.logger.info(f"Found {len(pairs)} release pairs: {pairs}")

        donePairs: list[ReleasePair] = []
        for pair in pairs:
            with indentLogging():
                try:
                    self.pair(pair)
                    donePairs.append(pair)
                except Exception as ex:
                    env.logger.error(f"Failed to process {pair=}", exc_info=ex)
        
        env.logger.info(
            f"Done {len(donePairs)} / {len([pairs])} pairs: {donePairs}"
        )

        self.index(project)

    def index(self, project: str):
        from .release import pair, sortedReleases

        env.logger.info(f"Index package {project}")

        releases = sortedReleases(self.getReleases(project))
        env.logger.info(f"Found {len(releases)} {releases=}")

        distributions = sortedReleases(self.dist.distributions(project))
        env.logger.info(f"Found {len(distributions)} {distributions=}")

        apis = sortedReleases(self.dist.apis(project))
        env.logger.info(f"Found {len(apis)} {apis=}")

        pairs = list(pair(apis))
        env.logger.info(f"Found {len(pairs)} {pairs=}")

        doneChanges = {str(x) for x in self.dist.changes(project)}
        changes = [x for x in pairs if str(x) in doneChanges]
        env.logger.info(f"Found {len(changes)} {changes=}")

        doneReports = {str(x) for x in self.dist.reports(project)}
        reports = [x for x in pairs if str(x) in doneReports]
        env.logger.info(f"Found {len(reports)} {reports=}")

        projectDir = self.dist.projectDir(project)
        utils.ensureDirectory(projectDir)
        wroteBytes = (projectDir / "index.json").write_text(
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
        env.logger.info(f"Saved index, {wroteBytes=}")

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
                    env.logger.error(
                        f"Failed to process package: {project}", exc_info=ex
                    )

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
