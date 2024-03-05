from . import DistStatistician
from aexpy.models import Distribution

S = DistStatistician()

from .shared import duration, success

S.count(duration)
S.count(success)
