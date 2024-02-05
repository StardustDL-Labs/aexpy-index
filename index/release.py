from datetime import datetime
import functools
from typing import Any, Callable
import requests
from . import env
import json
import re
import semver
from packaging import version as pkgVersion
from aexpy import utils
from aexpy.models import Release, ReleasePair

FILE_ORIGIN = "https://files.pythonhosted.org/"
FILE_TSINGHUA = "https://pypi.tuna.tsinghua.edu.cn/"
INDEX_ORIGIN = "https://pypi.org/simple/"
INDEX_TSINGHUA = "https://pypi.tuna.tsinghua.edu.cn/simple/"


def getIndex():
    url = INDEX_TSINGHUA if env.mirror else INDEX_ORIGIN
    resultCache = env.cache / "index.json"
    if resultCache.exists():
        return json.loads(resultCache.read_text())

    htmlCache = env.cache.joinpath("simple.html")
    if not htmlCache.exists():
        env.logger.info(f"Request PYPI Index @ {url}")
        htmlCache.write_text(requests.get(url, timeout=60).text)

    regex = r'<a href="[\w:/\.]*">([\S\s]*?)</a>'
    result = re.findall(regex, htmlCache.read_text())
    resultCache.write_text(json.dumps(result))
    return result


def getReleases(project: str) -> dict[str, Any]:
    cache = env.cache / "releases" / project
    utils.ensureDirectory(cache)
    cacheFile = cache / "index.json"
    if (
        cacheFile.exists()
        and (datetime.now().timestamp() - cacheFile.stat().st_mtime) / 60 / 60 / 24 <= 1
    ):
        return json.loads(cacheFile.read_text())

    url = f"https://pypi.org/pypi/{project}/json"
    env.logger.info(f"Request releases @ {url}")
    result = requests.get(url, timeout=60).json()["releases"]
    cacheFile.write_text(json.dumps(result))
    return result


def compareVersion(a, b):
    a = pkgVersion.parse(a)
    b = pkgVersion.parse(b)
    if a < b:
        return -1
    elif a > b:
        return 1
    else:
        return 0

def sortedVersions(releases: list[Release]):
    versions = releases.copy()
    try:
        versions.sort(
            key=functools.cmp_to_key(lambda x, y: compareVersion(x.version, y.version))
        )
    except Exception as ex:
        versions = releases.copy()
        env.logger.error(
            f"Failed to sort versions by packaging.version: {versions}", exc_info=ex
        )
        try:
            versions.sort(
                key=functools.cmp_to_key(
                    lambda x, y: semver.compare(x.version, y.version)
                )
            )
        except Exception as ex:
            versions = releases.copy()
            env.logger.error(
                f"Failed to sort versions by semver: {versions}", exc_info=ex
            )
    return versions


def single(project: str, filter: Callable[[Release], bool] | None = None):
    raw = getReleases(project)
    rels: list[Release] = []
    for version in raw:
        rel = Release(project=project, version=version)
        if filter:
            if not filter(rel):
                continue
        rels.append(rel)

    return sortedVersions(rels)


def pair(releases: list[Release], filter: Callable[[ReleasePair], bool] | None = None):
    ret: list[ReleasePair] = []

    lastVersion: Release | None = None
    for item in releases:
        if lastVersion is None:
            pass
        else:
            rp = ReleasePair(old=lastVersion, new=item)

            if filter:
                if not filter(rp):
                    continue

            ret.append(rp)
        lastVersion = item

    return ret
