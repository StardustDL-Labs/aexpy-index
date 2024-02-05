from contextlib import contextmanager
from functools import cached_property
import json
from pathlib import Path

from .dist import DistPathBuilder
from .aexpyw import AexPyWorker
from aexpy.models import Release, ReleasePair
from pydantic import BaseModel
from enum import IntEnum
from . import env


class ProcessState(IntEnum):
    SUCCESS = 1
    FAILURE = 2


class ProcessResult(BaseModel):
    version: str
    state: ProcessState


class ProcessDB(BaseModel):
    path: Path
    data: dict[str, ProcessResult] = {}

    def __getitem__(self, job: str):
        return self.data.get(job)

    @contextmanager
    def do(self, job: str, version: str):
        try:
            yield self[job]
            self.done(job, version, ProcessState.SUCCESS)
        except Exception as ex:
            env.logger.error(f"failed to do job: {job}", exc_info=ex)
            self.done(job, version, ProcessState.FAILURE)
            raise

    def done(self, job: str, version: str, state: ProcessState):
        self.data[job] = ProcessResult(version=version, state=state)

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

    @cached_property
    def workerVersion(self):
        return self.worker.version()

    @contextmanager
    def doOnce(self, type: str, id: str):
        with self.db.do(f"{type}:{id}", self.workerVersion) as res:
            if res:
                assert res.state == ProcessState.SUCCESS, "Not success"
                return
            yield

    def version(self, release: Release):
        env.logger.info(f"Process release {release}")
        dis = self.dist.preprocess(release)
        api = self.dist.extract(release)
        with self.doOnce(JOB_PREPROCESS, str(release)):
            env.logger.info(f"Preprocess release {release}")
            result = self.worker.preprocess(
                ["-r", "-p", str(release), str(env.cache), "-"]
            )
            result.ensure().save(dis)
        with self.doOnce(JOB_EXTRACT, str(release)):
            env.logger.info(f"Extract release {release}")
            result = self.worker.extract([str(dis), "-"])
            result.ensure().save(api)

    def pair(self, pair: ReleasePair):
        env.logger.info(f"Process release pair {pair}")
        old = self.dist.extract(pair.old)
        new = self.dist.extract(pair.new)
        cha = self.dist.diff(pair)
        rep = self.dist.report(pair)
        with self.doOnce(JOB_DIFF, str(pair)):
            env.logger.info(f"Diff releas pair {pair}")
            result = self.worker.diff([str(old), str(new), "-"])
            result.ensure().save(cha)
        with self.doOnce(JOB_REPORT, str(pair)):
            env.logger.info(f"Report releas pair {pair}")
            result = self.worker.report([str(cha), "-"])
            result.ensure().save(rep)

    def package(self, project: str):
        from .release import single, pair

        env.logger.info(f"Process package {project}")

        releases = single(project)
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
        from .release import single, pair
        env.logger.info(f"Index package {project}")

        releases = single(project)
        env.logger.info(f"Find {len(releases)} releases: {releases}")

        distributions = list(self.dist.distributions(project))
        apis = list(self.dist.apis(project))
        changes = list(self.dist.changes(project))
        reports = list(self.dist.reports(project))

        projectDir = self.dist.projectDir(project)
        (projectDir / "index.json").write_text(json.dumps({
            "releases": [str(r) for r in releases],
            "distributions": [str(r) for r in distributions],
            "apis": [str(r) for r in apis],
            "changes": [str(r) for r in changes],
            "reports": [str(r) for r in reports],
        }))