"""Local simulated sensor adapter; real ingestion requires a separate adapter."""
from __future__ import annotations
from datetime import datetime, timezone
import json
from typing import Protocol
from pydantic import BaseModel, Field

from .hydraulics import network, simulate
from .onboarding import DRAFT_DIR, draft_path


class Sensor(BaseModel):
    sensor_id: str = Field(min_length=1, max_length=40, pattern=r'^[A-Za-z0-9_-]+$')
    element_id: str
    kind: str  # pressure or flow
    unit: str  # m, kPa, L/s, m3/s
    interval_s: int = Field(default=10, ge=5, le=3600)


class SensorAdapter(Protocol):
    def read(self, network_name: str, sensors: list[Sensor]) -> list[dict]: ...


class SimulatedSensorAdapter:
    source = 'simulated'

    def read(self, network_name: str, sensors: list[Sensor]) -> list[dict]:
        observed = simulate(network_name)
        at = datetime.now(timezone.utc).isoformat()
        rows = []
        for sensor in sensors:
            value = observed['pressures' if sensor.kind == 'pressure' else 'flows'][sensor.element_id]
            if sensor.unit == 'kPa':
                value *= 9.80665
            elif sensor.unit == 'L/s':
                value *= 1000
            rows.append({'sensor_id': sensor.sensor_id, 'element_id': sensor.element_id,
                         'kind': sensor.kind, 'unit': sensor.unit, 'value': value,
                         'measured_at': at, 'quality': 'simulated', 'source': self.source})
        return rows


def validate_sensors(name: str, sensors: list[Sensor]):
    wn = network(name)
    if len(sensors) > 40:
        raise ValueError('At most 40 sensors can be registered')
    if len({s.sensor_id for s in sensors}) != len(sensors):
        raise ValueError('Sensor IDs must be unique')
    for sensor in sensors:
        allowed = wn.junction_name_list if sensor.kind == 'pressure' else wn.pipe_name_list if sensor.kind == 'flow' else []
        units = ('m', 'kPa') if sensor.kind == 'pressure' else ('L/s', 'm3/s')
        if sensor.element_id not in allowed or sensor.unit not in units:
            raise ValueError(f'{sensor.sensor_id}: choose a compatible location and unit')


def path_for(name):
    draft_path(name)  # validate ID and keep this local to household models
    return DRAFT_DIR / f'{name}.sensors.json'


def save_sensors(name, sensors):
    validate_sensors(name, sensors)
    path = path_for(name)
    path.write_text(json.dumps([s.model_dump() for s in sensors]))


def load_sensors(name):
    path = path_for(name)
    return [Sensor.model_validate(row) for row in json.loads(path.read_text())] if path.exists() else []
