from . import ChangeStatistician
from aexpy.models import ApiDifference
from aexpy.models.difference import BreakingRank

S = ChangeStatistician()

from .shared import duration, success

S.count(duration)
S.count(success)


@S.count
def kinds(data: ApiDifference):
    return {k: float(len(data.kind(k))) for k in data.kinds()}


@S.count
def breaking_kinds(data: ApiDifference):
    entries = data.breaking(BreakingRank.Low)
    return {
        k: float(sum(1 for e in entries if e.kind == k))
        for k in {x.kind for x in entries}
    }


@S.count
def ranks(data: ApiDifference):
    return {k.name: float(len(data.rank(k))) for k in BreakingRank}


@S.count
def breaking(data: ApiDifference):
    return sum(breaking_kinds(data).values())
