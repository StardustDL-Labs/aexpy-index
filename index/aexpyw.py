import os
from pathlib import Path
import subprocess
from dataclasses import dataclass
from aexpy.models import Product, Distribution, ApiDescription, ApiDifference, Report
from . import env
import sys


@dataclass
class AexPyResult[T: Product]:
    code: int
    log: str
    out: str
    data: T | None = None

    def ensure(self):
        if self.code != 0:
            env.logger.error(f"out: {self.out}")
            env.logger.error(f"log: {self.log}")
            assert False, f"Not success, exit code {self.code}"
        return self

    def save(self, path: Path):
        path.write_text(self.out)
        (path.with_suffix(".log")).write_text(self.log)


class AexPyWorker:
    __command_prefix__ = ["aexpy"]

    def run(self, args: list[str], **kwargs):
        return subprocess.run(
            self.__command_prefix__ + ["-vvvvv"] + args,
            text=True,
            encoding="utf-8",
            capture_output=True,
            env={**os.environ, "PYTHONUTF8": "1"},
            **kwargs,
        )

    def runParse[T: Product](self, type: type[T], args: list[str], **kwargs):
        res = self.run(args, **kwargs)
        result = AexPyResult[T](code=res.returncode, log=res.stderr, out=res.stdout)
        try:
            result.data = type.model_validate_json(result.out)
        except Exception as ex:
            env.logger.error("Failed to parse aexpy output", exc_info=ex)
            result.data = None
        return result

    def preprocess(self, args: list[str], **kwargs):
        return self.runParse(Distribution, ["preprocess"] + args, **kwargs)

    def extract(self, args: list[str], **kwargs):
        return self.runParse(ApiDescription, ["extract"] + args, **kwargs)

    def diff(self, args: list[str], **kwargs):
        return self.runParse(ApiDifference, ["diff"] + args, **kwargs)

    def report(self, args: list[str], **kwargs):
        return self.runParse(Report, ["report"] + args, **kwargs)

    def version(self):
        return (
            self.run(["--version"], check=True)
            .stdout.strip()
            .removeprefix("aexpy v")
            .removesuffix(".")
        )


class AexPyDockerWorker(AexPyWorker):
    __command_prefix__ = ["docker", "run", "--rm", "stardustdl/aexpy:latest"]
