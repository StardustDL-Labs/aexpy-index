import gzip
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
    log: bytes
    out: bytes
    data: T | None = None

    def ensure(self):
        if self.code != 0:
            assert False, f"Failed with exitcode {self.code}"
        return self

    def save(self, path: Path):
        path.write_bytes(self.out)
        (path.with_suffix(".log")).write_bytes(self.log)


class AexPyWorker:
    def __init__(self, compress: bool = False) -> None:
        self.compress = compress

    def getCommandPrefix(self):
        return ["aexpy"]

    def resolvePath(self, path: Path):
        return path

    def run(self, args: list[str], **kwargs) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            self.getCommandPrefix() + ["-vvvvv"] + args,
            capture_output=True,
            env={
                **os.environ,
                "PYTHONUTF8": "1",
                "AEXPY_GZIP_IO": "1" if self.compress else "0",
            },
            **kwargs,
        )

    def runParse[T: Product](self, type: type[T], args: list[str], **kwargs):
        res = self.run(args, **kwargs)
        result = AexPyResult[T](code=res.returncode, log=res.stderr, out=res.stdout)
        try:
            if self.compress:
                result.data = type.model_validate_json(gzip.decompress(result.out))
            else:
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
            .stdout.decode()
            .strip()
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
        ] + (["--gzip"] if self.compress else [])

    @override
    def resolvePath(self, path):
        return Path("/data/").joinpath(path.relative_to(env.cache))
