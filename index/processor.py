from contextlib import contextmanager
import datetime
from functools import cached_property
import json
import os
from pathlib import Path
import shutil
from typing import Iterable
from .dist import DistPathBuilder
from .worker import AexPyWorker
from aexpy.models import (
    Release,
    ReleasePair,
    Product,
    Distribution,
    Report,
    ApiDescription,
    ApiDifference,
)
from pydantic import BaseModel
from enum import IntEnum
from . import env, indentLogging
from .stats import StatisticianWorker
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
    name: str = "aexpy-index"
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
        except Exception:
            env.logger.error(f"failed to do job: {job}", exc_info=True)
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
        except Exception:
            env.logger.error(
                f"failed to load process db: {file}, use empty db", exc_info=True
            )
            res = cls(name="", path=file)
        res.processCount = 0
        return res


JOB_EXTRACT = "extract"
JOB_DIFF = "diff"


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

    def doOnce(
        self, type: str, id: str
    ):  # -> ProcessResult | Callable[[], _GeneratorContextManager[None]]:
        if self.hasDone(type, id):
            res = self.db[f"{type}:{id}"]
            assert res is not None
            return res

        @contextmanager
        def wrapper():
            with self.db.do(f"{type}:{id}", self.workerVersion):
                yield

        return wrapper

    def processVersion(self, release: Release):
        env.logger.info(f"Preprocess release {str(release)}")
        dis = self.cacheDist.preprocess(release)
        wheelDir = self.cacheDist.projectDir(release.project) / "wheels"
        utils.ensureDirectory(wheelDir)
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

        env.logger.info(f"Extract release {str(release)}")
        api = self.cacheDist.extract(release)
        result = self.worker.extract([str(self.worker.resolvePath(dis)), "-"])
        result.save(api)
        result.ensure().save(self.dist.extract(release))

        shutil.rmtree(wheelDir, ignore_errors=True)

    def processPair(self, pair: ReleasePair):
        old = self.cacheDist.extract(pair.old)
        new = self.cacheDist.extract(pair.new)
        if not old.is_file():
            old.write_bytes(self.dist.extract(pair.old).read_bytes())
        if not new.is_file():
            new.write_bytes(self.dist.extract(pair.new).read_bytes())

        env.logger.info(f"Diff pair {str(pair)}")
        cha = self.cacheDist.diff(pair)
        result = self.worker.diff(
            [
                str(self.worker.resolvePath(old)),
                str(self.worker.resolvePath(new)),
                "-",
            ]
        )
        result.save(cha)
        result.ensure().save(self.dist.diff(pair))

        env.logger.info(f"Report pair {str(pair)}")
        rep = self.cacheDist.report(pair)
        result = self.worker.report([str(self.worker.resolvePath(cha)), "-"])
        result.save(rep)
        result.ensure().save(self.dist.report(pair))

    def version(self, release: Release):
        wrapper = self.doOnce(JOB_EXTRACT, str(release))
        if isinstance(wrapper, ProcessResult):
            env.logger.info(f"Preprocessed release {str(release)}")
            assert wrapper.state == ProcessState.SUCCESS, "not success"
            return
        with wrapper():
            self.processVersion(release)

    def pair(self, pair: ReleasePair):
        wrapper = self.doOnce(JOB_DIFF, str(pair))
        if isinstance(wrapper, ProcessResult):
            env.logger.info(f"Diffed pair {str(pair)}")
            assert wrapper.state == ProcessState.SUCCESS, "not success"
            return

        with wrapper():
            self.processPair(pair)

    def getReleases(self, project: str):
        from .release import single

        return single(project)[-40:]

    def package(self, project: str):
        from .release import pair

        env.logger.info(f"Process package {project}")

        with indentLogging(f"Package: {project}"):
            releases = self.getReleases(project)
            env.logger.info(
                f"Found {len(releases)} releases: {', '.join(str(r) for r in releases).replace(f'{project}@', '')}"
            )

        doneReleases: list[Release] = []
        for i, release in enumerate(releases):
            env.logger.info(f"({i+1} / {len(releases)}) Version {str(release)}")
            with indentLogging(f"Version: {str(release)}"):
                try:
                    self.version(release)
                    doneReleases.append(release)
                except Exception:
                    env.logger.error(
                        f"Failed to process release {str(release)}", exc_info=True
                    )

        env.logger.info(
            f"Done {len(doneReleases)} / {len(releases)} releases: {', '.join(str(r) for r in doneReleases).replace(f'{project}@', '')}"
        )

        pairs = pair(doneReleases)
        env.logger.info(
            f"Found {len(pairs)} pairs: {', '.join(str(r) for r in pairs).replace(f'{project}@', '')}"
        )

        donePairs: list[ReleasePair] = []
        for i, pair in enumerate(pairs):
            env.logger.info(f"({i+1} / {len(pairs)}) Pair {str(pair)}")
            with indentLogging(f"Pair: {str(pair)}"):
                try:
                    self.pair(pair)
                    donePairs.append(pair)
                except Exception:
                    env.logger.error(
                        f"Failed to process pair {str(pair)}", exc_info=True
                    )

        env.logger.info(
            f"Done {len(donePairs)} / {len(pairs)} pairs: {', '.join(str(r) for r in donePairs).replace(f'{project}@', '')}"
        )

        self.index(project)

    def cleanLoad[T: Product](self, type: type[T], paths: Iterable[Path]):
        from aexpy.io import load

        for path in paths:
            try:
                yield load(path, type)
            except Exception:
                env.logger.error(
                    f"Failed to load {type.__class__.__qualname__} from {path}",
                    exc_info=True,
                )
                env.logger.warning(f"Remove {path} because of the loading failure.")
                os.remove(path)

    def index(self, project: str):
        from .stats import (
            dists as distS,
            apis as apiS,
            reports as reportS,
            changes as changeS,
        )
        from .release import pair, sortedReleases

        projectDir = self.dist.projectDir(project)
        utils.ensureDirectory(projectDir)

        env.logger.info(f"Index package {project}")

        releases = sortedReleases(self.getReleases(project))
        env.logger.info(
            f"Found {len(releases)} releases: {', '.join(str(r) for r in releases).replace(f'{project}@', '')}"
        )

        distributions = sortedReleases(self.dist.distributions(project))
        env.logger.info(
            f"Found {len(distributions)} distributions: {', '.join(str(r) for r in distributions).replace(f'{project}@', '')}"
        )
        loaded = list(
            self.cleanLoad(
                Distribution, (self.dist.preprocess(r) for r in distributions)
            )
        )
        distributions = [f.single() for f in loaded]
        env.logger.info(
            f"Loaded {len(distributions)} distributions: {', '.join(str(r) for r in distributions).replace(f'{project}@', '')}"
        )
        StatisticianWorker(Distribution, distS.S, projectDir / "dists.json").process(
            loaded
        ).save()

        apis = sortedReleases(self.dist.apis(project))
        env.logger.info(
            f"Found {len(apis)} apis: {', '.join(str(r) for r in apis).replace(f'{project}@', '')}"
        )
        loaded = list(
            self.cleanLoad(ApiDescription, (self.dist.extract(r) for r in apis))
        )
        apis = [f.single() for f in loaded]
        env.logger.info(
            f"Loaded {len(apis)} apis: {', '.join(str(r) for r in apis).replace(f'{project}@', '')}"
        )
        StatisticianWorker(ApiDescription, apiS.S, projectDir / "apis.json").process(
            loaded
        ).save()

        pairs = list(pair(apis))
        env.logger.info(
            f"Found {len(pairs)} pairs: {', '.join(str(r) for r in pairs).replace(f'{project}@', '')}"
        )

        doneChanges = {str(x) for x in self.dist.changes(project)}
        changes = [x for x in pairs if str(x) in doneChanges]
        env.logger.info(
            f"Found {len(changes)} changes: {', '.join(str(r) for r in changes).replace(f'{project}@', '')}"
        )
        loaded = list(
            self.cleanLoad(ApiDifference, (self.dist.diff(r) for r in changes))
        )
        changes = [f.pair() for f in loaded]
        env.logger.info(
            f"Loaded {len(changes)} changes: {', '.join(str(r) for r in changes).replace(f'{project}@', '')}"
        )
        StatisticianWorker(
            ApiDifference, changeS.S, projectDir / "changes.json"
        ).process(loaded).save()

        doneReports = {str(x) for x in self.dist.reports(project)}
        reports = [x for x in pairs if str(x) in doneReports]
        env.logger.info(
            f"Found {len(reports)} reports: {', '.join(str(r) for r in reports).replace(f'{project}@', '')}"
        )
        loaded = list(self.cleanLoad(Report, (self.dist.report(r) for r in reports)))
        reports = [f.pair() for f in loaded]
        env.logger.info(
            f"Loaded {len(reports)} reports: {', '.join(str(r) for r in reports).replace(f'{project}@', '')}"
        )
        StatisticianWorker(Report, reportS.S, projectDir / "reports.json").process(
            loaded
        ).save()

        releases = sortedReleases(set(releases) | set(distributions) | set(apis))

        wroteBytes = (projectDir / "index.json").write_text(
            json.dumps(
                {
                    "releases": [str(r) for r in releases],
                    "distributions": [str(r) for r in distributions],
                    "apis": [str(r) for r in apis],
                    "pairs": [
                        f"{r.old.project}@{r.old.version}&{r.new.version}"
                        for r in pairs
                    ],
                    "changes": [
                        f"{r.old.project}@{r.old.version}&{r.new.version}"
                        for r in changes
                    ],
                    "reports": [
                        f"{r.old.project}@{r.old.version}&{r.new.version}"
                        for r in reports
                    ],
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

                        std = StdProcessor(self.worker, self.db, self.dist)
                        std.package(project)
                    doneProjects.append(project)
                except Exception:
                    env.logger.error(
                        f"Failed to process package: {project}", exc_info=True
                    )

    def indexPackages(self):
        doneProjects: list[str] = []
        for project in self.dist.projects():
            try:
                if project != "python":
                    self.index(project)
                else:
                    from .std import StdProcessor

                    std = StdProcessor(self.worker, self.db, self.dist)
                    std.index(project)
                doneProjects.append(project)
            except Exception:
                env.logger.error(f"Failed to index package: {project}", exc_info=True)
        (env.dist / "packages.json").write_text(json.dumps(doneProjects))
