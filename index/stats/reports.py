from . import ReportStatistician
from aexpy.models import Report

S = ReportStatistician()

from .shared import duration, success

S.count(duration)
S.count(success)
