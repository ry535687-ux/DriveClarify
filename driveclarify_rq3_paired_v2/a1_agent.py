"""A1：冻结 DriveClarifyRQ3 路径，仅共用场景装配和只读全程观测。"""
from driveclarify_rq3.simlingo_agent import DriveClarifyRQ3SimLingoAgent
from driveclarify_rq3_paired_v2.runtime_common import PairedObservationMixin


def get_entry_point():return 'V2DriveClarifyA1Agent'


class V2DriveClarifyA1Agent(PairedObservationMixin,DriveClarifyRQ3SimLingoAgent):
    pass
