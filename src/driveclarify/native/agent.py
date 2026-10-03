"""native.agent implementation."""
from driveclarify.runtime.native_binding import InstructionBindingMixin
from driveclarify.native.observation import PairedObservationMixin
from driveclarify.native.temporal_agent import TemporalSimLingoAgent


class DriveClarifyAgent(InstructionBindingMixin,PairedObservationMixin,TemporalSimLingoAgent):
    pass


def get_entry_point():
    return 'DriveClarifyAgent'
