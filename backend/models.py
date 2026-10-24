"""Fault scenario contract; severities in the offline signature grid."""
from dataclasses import dataclass

SEVERITIES = (0.05, 0.20, 0.35, 0.70, 1.0)

@dataclass(frozen=True)
class Candidate:
    fault_type: str
    location_id: str
    severity: float

    @property
    def key(self):
        return f'{self.fault_type}:{self.location_id}:{self.severity}'
