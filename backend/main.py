"""FastAPI entrypoint. POST /api/investigate is synchronous; session API streams progress."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import asyncio
import json
import os
import time
from pathlib import Path
from functools import lru_cache
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator
from typing import Literal
from .hydraulics import network, topology, simulate, simulate_series
from .models import Candidate
from .fingerprints import load_catalog
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / '.env', override=False)
except ImportError:
    pass

if os.getenv('AGENTOPS_API_KEY'):
    try:
        import agentops
        agentops.init(api_key=os.environ['AGENTOPS_API_KEY'], auto_start_session=False)
    except ImportError:
        pass  # /api/observability reports the missing optional package.

from .graph import GRAPH
from .observability import status as tracing_status, trace_workflow

app = FastAPI(title='WaterGuard', version='1.0')
POOL = ThreadPoolExecutor(max_workers=2)
SESSIONS = {}

class Readings(BaseModel):
    pressures: dict[str, float] = Field(default_factory=dict)
    flows: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode='after')
    def nonempty(self):
        if not self.pressures and not self.flows:
            raise ValueError('At least one pressure or flow sensor is needed')
        return self

class ComparisonRequest(BaseModel):
    network_name: str = 'Net1'
    candidate: dict

class CustomFault(BaseModel):
    fault_type: Literal['pipe_leak', 'valve_closure', 'demand_surge']
    location_id: str
    severity: float = Field(ge=0.05, le=1.0)

class Request(BaseModel):
    network_name: str = 'Net1'
    observed: Readings
    budget: int = Field(default=8, ge=2, le=24)


def validate(req: Request):
    try:
        wn = network(req.network_name)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    invalid = (set(req.observed.pressures) - set(wn.junction_name_list)) | (
        set(req.observed.flows) - set(wn.pipe_name_list))
    if invalid:
        raise HTTPException(422, f'Unknown sensor identifiers: {sorted(invalid)}')

@app.get('/api/network/{name}')
def get_network(name: str):
    try:
        return topology(name)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e

@app.get('/api/baseline/{name}')
def get_baseline(name: str):
    try:
        catalog = load_catalog(name)
        return {'network_name': name, 'readings': catalog['baseline'],
                'offline_simulation_count': catalog['offline_simulation_count']}
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(404, str(e)) from e

@app.get('/api/observability')
def get_observability():
    return tracing_status()

@lru_cache(maxsize=8)
def series_baseline(name: str):
    return simulate_series(name)

@app.get('/api/demo/{kind}/series')
def demo_series(kind: str):
    samples = {'leak': Candidate('pipe_leak', '11', .55),
               'valve': Candidate('valve_closure', '21', .55),
               'surge': Candidate('demand_surge', '12', .55)}
    if kind not in samples:
        raise HTTPException(404, 'Unknown demo kind')
    return {'baseline': series_baseline('Net1'),
            'fault': simulate_series('Net1', samples[kind]),
            'note': 'Two independent model projections. Fault is active from hour 0; diagnosis scores hour 0.'}

@app.post('/api/compare-series')
def compare_series(req: ComparisonRequest):
    try:
        wn = network(req.network_name)
        h = Candidate(**req.candidate)
        if not 0 < h.severity <= 1:
            raise ValueError('Severity must be between 0 and 1')
        if h.fault_type in ('pipe_leak', 'valve_closure') and h.location_id not in wn.pipe_name_list:
            raise ValueError('Unknown pipe')
        if h.fault_type == 'demand_surge' and h.location_id not in wn.junction_name_list:
            raise ValueError('Unknown junction')
        return {'baseline': series_baseline(req.network_name),
                'fault': simulate_series(req.network_name, h),
                'note': 'Two independent model projections. Candidate is active from hour 0; diagnosis scores hour 0.'}
    except (ValueError, TypeError) as e:
        raise HTTPException(422, str(e)) from e

@app.get('/api/demo/{kind}')
def demo(kind: str):
    # Ground truth is deliberately kept separate from investigation response.
    samples = {'leak': Candidate('pipe_leak', '11', 0.55),
               'valve': Candidate('valve_closure', '21', 0.55),
               'surge': Candidate('demand_surge', '12', 0.55)}
    if kind not in samples:
        raise HTTPException(404, 'Unknown demo kind')
    try:
        obs = simulate('Net1', samples[kind])
    except Exception as e:
        raise HTTPException(500, f'Demo simulation failed: {e}') from e
    # Fixed sensor set allows a reproducible comparison, no hardcoded values.
    nodes = list(obs['pressures'])
    pipes = list(obs['flows'])
    return {'network_name': 'Net1', 'observed': {'pressures': {n: obs['pressures'][n] for n in nodes},
                                                  'flows': {p: obs['flows'][p] for p in pipes}},
            'ground_truth': vars(samples[kind])}

@app.post('/api/demo/custom')
def custom_demo(req: CustomFault):
    wn = network('Net1')
    allowed = wn.junction_name_list if req.fault_type == 'demand_surge' else wn.pipe_name_list
    if req.location_id not in allowed:
        raise HTTPException(422, 'Select a valid junction for demand or a valid pipe for leak/restriction')
    candidate = Candidate(req.fault_type, req.location_id, req.severity)
    try:
        observed = simulate('Net1', candidate)
        series = {'baseline': series_baseline('Net1'), 'fault': simulate_series('Net1', candidate),
                  'note': 'Independent healthy and fault projections; diagnosis scores hour 0.'}
    except Exception as e:
        raise HTTPException(422, f'Could not simulate selected fault: {e}') from e
    return {'network_name': 'Net1', 'observed': observed,
            'ground_truth': vars(candidate), 'series': series}

@app.post('/api/investigate')
def investigate(req: Request):
    validate(req)
    try:
        return trace_workflow(lambda: GRAPH.invoke({'network_name': req.network_name,
            'observed': req.observed.model_dump(), 'budget': req.budget,
            'rows': [], 'pending': [], 'events': []},
            config={'recursion_limit': 50, 'run_name': 'WaterGuard investigation',
                    'tags': ['waterguard', req.network_name]})['report'])
    except Exception as e:
        raise HTTPException(500, f'Investigation failed: {e}') from e

@app.post('/api/investigations', status_code=202)
async def create_session(req: Request):
    validate(req)
    sid = str(uuid4())
    SESSIONS[sid] = {'events': [], 'status': 'running', 'result': None}
    def run():
        started = time.monotonic()
        state = {'network_name': req.network_name, 'observed': req.observed.model_dump(),
                 'budget': req.budget, 'rows': [], 'pending': [], 'events': []}
        def execute_graph():
            for update in GRAPH.stream(state, config={'recursion_limit': 50,
                    'run_name': 'WaterGuard investigation',
                    'tags': ['waterguard', req.network_name],
                    'metadata': {'session_id': sid}}, stream_mode='updates'):
                for node, delta in update.items():
                    event = {'type': 'node', 'node': node,
                             'elapsed_ms': round((time.monotonic() - started) * 1000)}
                    if node == 'simulate':
                        event['simulation_count'] = len(delta['rows'])
                        event['latest'] = delta['events'][-1]['tested']
                    if node == 'monitor':
                        event['anomaly'] = delta['alert']
                    elif node == 'context':
                        event['sources'] = {k: len(v) for k, v in delta['context'].items()}
                    elif node == 'hypotheses':
                        event['shortlist'] = [x['candidate'] for x in delta['pending'][:3]]
                        event['shortlist_count'] = len(delta['pending'])
                    elif node == 'evidence':
                        event['leaders'] = [{'candidate': r['candidate'], 'score': r['score']}
                                            for r in delta['diagnosis']['ranked'][:2]]
                        event['ambiguous'] = delta['diagnosis']['ambiguous']
                    elif node == 'diagnose':
                        event['action'] = delta['recommendation']['action']
                    elif node == 'sensor_planner':
                        event['sensor_recommendation'] = delta['sensor_recommendation']
                    SESSIONS[sid]['events'].append(event)
                    if node == 'report':
                        SESSIONS[sid]['result'] = delta['report']
        try:
            trace_workflow(execute_graph)
            SESSIONS[sid]['status'] = 'complete'
            SESSIONS[sid]['events'].append({'type': 'complete'})
        except Exception as e:
            SESSIONS[sid]['status'] = 'error'
            SESSIONS[sid]['events'].append({'type': 'error', 'message': str(e)})
    POOL.submit(run)
    return {'session_id': sid}

@app.get('/api/investigations/{sid}')
def get_session(sid: str):
    if sid not in SESSIONS:
        raise HTTPException(404, 'Session not found')
    d = SESSIONS[sid]
    return {'status': d['status'], 'result': d['result']}

@app.get('/api/investigations/{sid}/events')
async def events(sid: str):
    if sid not in SESSIONS:
        raise HTTPException(404, 'Session not found')
    session = SESSIONS[sid]
    async def stream():
        yield 'data: ' + json.dumps({'type': 'connected'}) + '\n\n'
        index = 0
        while True:
            journal = session['events']
            if index < len(journal):
                item = journal[index]
                index += 1
                yield 'data: ' + json.dumps(item) + '\n\n'
                if item['type'] in ('complete', 'error'):
                    break
            else:
                await asyncio.sleep(0.1)
    return StreamingResponse(stream(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache'})

# The plain HTML/CSS/JS client is served by the same origin as the API.
from fastapi.staticfiles import StaticFiles
app.mount('/', StaticFiles(directory=Path(__file__).resolve().parents[1] / 'frontend', html=True), name='frontend')
