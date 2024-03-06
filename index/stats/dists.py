from . import DistStatistician
from aexpy.models import Distribution

S = DistStatistician()

from .shared import duration, success

S.count(duration)
S.count(success)


@S.count
def loc(data: Distribution):
    return data.locCount


@S.count
def filesize(data: Distribution):
    return data.fileSize


@S.count
def filecount(data: Distribution):
    return data.fileCount
