import os
from pathlib import Path
import subprocess
from dataclasses import dataclass
from typing import override
from aexpy.models import Product, Distribution, ApiDescription, ApiDifference, Report
import aexpy
from . import env


@dataclass
class AexPyResult[T: Product]:
    code: int
    log: str
    out: str
    data: T | None = None

    def ensure(self):
        if self.code != 0:
            assert False, f"Failed with exitcode {self.code}"
        return self

    def save(self, path: Path):
        path.write_text(self.out)
        (path.with_suffix(".log")).write_text(self.log)


class AexPyWorker:
    def getCommandPrefix(self):
        return ["aexpy"]

    def resolvePath(self, path: Path):
        return path

    def run(self, args: list[str], **kwargs):
        return subprocess.run(
            self.getCommandPrefix() + ["-vvvvv"] + args,
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
        except Exception:
            env.logger.error("Failed to parse aexpy output", exc_info=True)
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
    @override
    def getCommandPrefix(self):
        return [
            "docker",
            "run",
            "-v",
            f"{str(env.cache.resolve())}:/data",
            "-u",
            "root",
            "--rm",
            f"stardustdl/aexpy:v{aexpy.__version__}",
        ]

    @override
    def resolvePath(self, path):
        return Path("/data/").joinpath(path.relative_to(env.cache))
