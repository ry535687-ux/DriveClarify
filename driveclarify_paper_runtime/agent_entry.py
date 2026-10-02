"""独立开发入口；不是已经获得原生场景资格或跑完的新正式方法。"""
from driveclarify_paper_runtime.native_binding import PaperInstructionBindingMixin
from driveclarify_rq3_paired_v2.runtime_common import PairedObservationMixin
from driveclarify_rq3.simlingo_agent import DriveClarifyRQ3SimLingoAgent


class PaperPairedAgent(PaperInstructionBindingMixin,PairedObservationMixin,DriveClarifyRQ3SimLingoAgent):
    pass


def get_entry_point():
    return 'PaperPairedAgent'
