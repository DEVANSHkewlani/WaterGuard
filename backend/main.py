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
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request as FastAPIRequest
from fastapi.responses import StreamingResponse
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator
from typing import Literal
import re
import math
from datetime import datetime
from .hydraulics import network as load_network, topology, simulate, simulate_series, available_networks
from .models import Candidate
from .fingerprints import load_catalog, build_catalog
from .impact import analyze_impact
from .onboarding import (Draft, guided_draft, inspect_document, hydraulic_check,
                         new_draft, save_draft, load_draft, to_wntr, DRAFT_DIR,
                         draft_path)
from .hydraulics import UPLOADS_DIR
from .sensors import Sensor, SimulatedSensorAdapter, save_sensors, load_sensors
import wntr
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

@app.middleware("http")
async def add_no_cache_headers(request, call_next):
    owner = request.cookies.get('wg_owner')
    if not owner or not re.fullmatch(r'[0-9a-f]{32}', owner):
        owner = uuid4().hex
    request.state.owner = owner
    response = await call_next(request)
    if request.cookies.get('wg_owner') != owner:
        response.set_cookie('wg_owner', owner, httponly=True, samesite='lax', max_age=60*60*24*365)
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

POOL = ThreadPoolExecutor(max_workers=2)
SESSIONS = {}
CATALOG_JOBS = {}
HISTORY_DIR = Path(__file__).resolve().parents[1] / 'data' / 'investigations'

def require_network(name: str, owner: str):
    if name in ('Net1', 'Net2'):
        return
    if name.startswith('H_'):
        try:
            load_draft(name, owner)
        except (ValueError, FileNotFoundError, PermissionError) as exc:
            raise HTTPException(404, 'Network not found for this browser') from exc
        return
    marker = UPLOADS_DIR / f'{name}.owner'
    if marker.exists() and marker.read_text() == owner:
        return
    raise HTTPException(404, 'Network not found for this browser')

class Readings(BaseModel):
    pressures: dict[str, float] = Field(default_factory=dict)
    flows: dict[str, float] = Field(default_factory=dict)
    pressure_unit: Literal['m'] = 'm'
    flow_unit: Literal['m3/s'] = 'm3/s'

    @model_validator(mode='after')
    def nonempty(self):
        if not self.pressures and not self.flows:
            raise ValueError('At least one pressure or flow sensor is needed')
        if any(not math.isfinite(value) for value in list(self.pressures.values()) + list(self.flows.values())):
            raise ValueError('Readings must be finite numbers')
        return self

class ComparisonRequest(BaseModel):
    network_name: str = 'Net1'
    candidate: dict

class CustomFault(BaseModel):
    network_name: str = 'Net1'
    fault_type: Literal['pipe_leak', 'valve_closure', 'demand_surge']
    location_id: str
    severity: float = Field(ge=0.05, le=1.0)

class Request(BaseModel):
    network_name: str = 'Net1'
    observed: Readings
    budget: int = Field(default=8, ge=2, le=24)
    observation_source: Literal['manual', 'simulated', 'sensor'] = 'manual'
    measured_at: str | None = None
    sensor_quality: Literal['unknown', 'checked', 'uncertain'] = 'unknown'
    operating_conditions: str | None = Field(default=None, max_length=500)

    @model_validator(mode='after')
    def valid_time(self):
        if self.measured_at:
            try:
                datetime.fromisoformat(self.measured_at)
            except ValueError as exc:
                raise ValueError('Use an ISO measurement timestamp') from exc
        return self


def validate(req: Request, owner: str | None = None):
    if req.observation_source == 'sensor':
        raise HTTPException(422, 'Real sensor ingestion is not configured; use manual or simulated readings')
    if owner is not None:
        require_network(req.network_name, owner)
    job = CATALOG_JOBS.get(req.network_name)
    if job and job['status'] != 'ready':
        raise HTTPException(409, 'This network catalog is still building' if job['status'] == 'building'
                            else f'This network catalog failed: {job.get("error", "unknown error")}')
    try:
        wn = load_network(req.network_name)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    invalid = (set(req.observed.pressures) - set(wn.junction_name_list)) | (
        set(req.observed.flows) - set(wn.pipe_name_list))
    if invalid:
        raise HTTPException(422, f'Unknown sensor identifiers: {sorted(invalid)}')

@app.get('/api/networks')
def list_networks(request: FastAPIRequest):
    return {'networks': [n for n in available_networks() if n in ('Net1', 'Net2') or
            (n.startswith('H_') and draft_path(n).exists() and
             json.loads(draft_path(n).read_text()).get('owner') == request.state.owner) or
            (UPLOADS_DIR / f'{n}.owner').exists() and
             (UPLOADS_DIR / f'{n}.owner').read_text() == request.state.owner]}

@app.post('/api/onboarding/diagram')
async def upload_diagram(request: FastAPIRequest, file: UploadFile = File(...),
                         consent_external: bool = Form(False)):
    content = await file.read(10 * 1024 * 1024 + 1)
    try:
        info = inspect_document(file.filename or '', content)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    draft = new_draft('diagram', Path(file.filename or 'My network').stem)
    draft.document = {**info, 'interpretation': 'Manual tracing required; no connectivity was inferred'}
    message = 'Diagram accepted. Trace the source, outlets, and each connection, then review measurements.'
    if consent_external and os.getenv('WATERGUARD_VISION_MODEL') and os.getenv('OPENAI_API_KEY'):
        try:
            from .vision import propose_draft
            draft = await asyncio.to_thread(propose_draft, draft, content)
            message = 'AI proposed a draft. Confirm every point, connection, and measurement before validation.'
        except Exception as exc:
            message = f'AI interpretation was unavailable ({exc}). Trace the diagram manually.'
    save_draft(draft, request.state.owner)
    (DRAFT_DIR / f'{draft.id}.{info["type"]}').write_bytes(content)
    return {'draft': draft.model_dump(), 'message': message}

@app.post('/api/onboarding/new')
def create_manual(request: FastAPIRequest):
    draft = new_draft('manual')
    save_draft(draft, request.state.owner)
    return {'draft': draft.model_dump()}

@app.get('/api/onboarding/document/{name}')
def get_document(name: str, request: FastAPIRequest):
    try:
        draft, _ = load_draft(name, request.state.owner)
    except (ValueError, FileNotFoundError, PermissionError) as exc:
        raise HTTPException(404, 'Drawing not found for this browser') from exc
    kind = draft.document.get('type')
    if kind not in ('pdf', 'png', 'jpeg'):
        raise HTTPException(404, 'No drawing for this network')
    source_name = draft.document.get('source_draft_id', name)
    if source_name != name:
        load_draft(source_name, request.state.owner)
    path = DRAFT_DIR / f'{source_name}.{kind}'
    if not path.exists():
        raise HTTPException(404, 'Drawing is missing')
    return FileResponse(path, media_type={'pdf': 'application/pdf', 'png': 'image/png',
                                          'jpeg': 'image/jpeg'}[kind])

@app.post('/api/onboarding/guided')
def guided(request: FastAPIRequest, answers: dict):
    draft = guided_draft(answers)
    save_draft(draft, request.state.owner)
    return {'draft': draft.model_dump()}

@app.post('/api/onboarding/draft')
def update_draft(request: FastAPIRequest, draft: Draft):
    load_draft(draft.id, request.state.owner)
    save_draft(draft, request.state.owner)
    return {'draft': draft.model_dump()}

@app.get('/api/onboarding/draft/{name}')
def get_draft(name: str, request: FastAPIRequest):
    try:
        draft, validation = load_draft(name, request.state.owner)
        return {'draft': draft.model_dump(), 'validation': validation}
    except (ValueError, FileNotFoundError, PermissionError) as exc:
        raise HTTPException(404, str(exc)) from exc

class ModelRequest(BaseModel):
    draft: Draft
    estimated: bool = False

@app.post('/api/onboarding/validate')
def validate_household(request: FastAPIRequest, body: ModelRequest):
    try:
        load_draft(body.draft.id, request.state.owner)
        check = hydraulic_check(body.draft, body.estimated)
        save_draft(body.draft, request.state.owner, check)
        return check
    except (ValueError, FileNotFoundError, PermissionError) as exc:
        raise HTTPException(422, str(exc)) from exc

@app.post('/api/onboarding/commit', status_code=202)
def commit_household(request: FastAPIRequest, body: ModelRequest):
    try:
        load_draft(body.draft.id, request.state.owner)
        check = hydraulic_check(body.draft, body.estimated)
        if not check['hydraulic_valid']:
            raise ValueError('; '.join(check['errors']))
        wn, _ = to_wntr(body.draft, body.estimated)
        # Limit catalog cost before publishing this model.
        count = 5 * (2 * len(wn.pipe_name_list) + len(wn.junction_name_list))
        if count > 300:
            raise ValueError(f'{count} scenarios exceed the 300 scenario limit; simplify this network')
        # Published model IDs are immutable versions. The editable source draft remains separate.
        version = 'H_' + uuid4().hex[:16]
        published = body.draft.model_copy(deep=True, update={'id': version,
            'document': {**body.draft.document, 'source_draft_id': body.draft.id}})
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        dest = UPLOADS_DIR / f'{version}.inp'
        wntr.network.write_inpfile(wn, str(dest))
        save_draft(published, request.state.owner, check)
        series_baseline.cache_clear()
        job = {'status': 'building', 'done': 0, 'total': count, 'network_name': version}
        CATALOG_JOBS[version] = job
        def build():
            try:
                build_catalog(version, lambda done, total: job.update(done=done, total=total), max_candidates=300)
                job['status'] = 'ready'
            except Exception as exc:
                job.update(status='error', error=str(exc))
                dest.unlink(missing_ok=True)
        POOL.submit(build)
        return {'network_name': version, 'validation': check, 'catalog': job}
    except (ValueError, FileNotFoundError, PermissionError) as exc:
        raise HTTPException(422, str(exc)) from exc

@app.get('/api/onboarding/catalog/{name}')
def catalog_progress(name: str, request: FastAPIRequest):
    require_network(name, request.state.owner)
    job = CATALOG_JOBS.get(name)
    if job:
        if job['status'] == 'ready':
            try:
                load_catalog(name)
            except Exception as exc:
                return {'status': 'error', 'error': str(exc), 'network_name': name}
        return job
    try:
        load_catalog(name)
        return {'status': 'ready', 'network_name': name}
    except Exception as exc:
        return {'status': 'error', 'error': str(exc), 'network_name': name}

@app.delete('/api/onboarding/network/{name}')
def delete_household(name: str, request: FastAPIRequest):
    require_network(name, request.state.owner)
    if name in ('Net1', 'Net2'):
        raise HTTPException(403, 'Built-in examples cannot be deleted')
    if CATALOG_JOBS.get(name, {}).get('status') == 'building' or any(
        session.get('network_name') == name and session.get('status') == 'running'
        for session in SESSIONS.values()):
        raise HTTPException(409, 'Wait for the active model job or investigation to finish')
    source_id = None
    if name.startswith('H_'):
        source_id = load_draft(name, request.state.owner)[0].document.get('source_draft_id')
    for path in (UPLOADS_DIR / f'{name}.inp', UPLOADS_DIR / f'{name}.owner',
                 Path(__file__).resolve().parents[1] / 'data' / f'{name}_fingerprints.json'):
        path.unlink(missing_ok=True)
    if name.startswith('H_'):
        draft_path(name).unlink(missing_ok=True)
        for path in DRAFT_DIR.glob(f'{name}.*'):
            path.unlink(missing_ok=True)
        if source_id:
            # Remove the original drawing when no other published version uses it.
            in_use = any(json.loads(path.read_text()).get('draft', {}).get('document', {}).get('source_draft_id') == source_id
                         and (UPLOADS_DIR / f'{path.stem}.inp').exists()
                         for path in DRAFT_DIR.glob('H_*.json') if not path.name.endswith('.sensors.json'))
            if not in_use:
                for path in DRAFT_DIR.glob(f'{source_id}.*'):
                    path.unlink(missing_ok=True)
    CATALOG_JOBS.pop(name, None)
    series_baseline.cache_clear()
    for sid in [k for k, v in SESSIONS.items() if v.get('network_name') == name]:
        SESSIONS.pop(sid, None)
    if HISTORY_DIR.exists():
        for path in HISTORY_DIR.glob('*.json'):
            try:
                if json.loads(path.read_text()).get('network_name') == name:
                    path.unlink()
            except (ValueError, OSError):
                pass
    return {'deleted': name}

class SensorSetup(BaseModel):
    sensors: list[Sensor]

@app.put('/api/onboarding/sensors/{name}')
def configure_sensors(name: str, body: SensorSetup, request: FastAPIRequest):
    require_network(name, request.state.owner)
    if not name.startswith('H_'):
        raise HTTPException(422, 'Use a household model for sensor setup')
    try:
        save_sensors(name, body.sensors)
        return {'network_name': name, 'source': 'simulated', 'sensors': [s.model_dump() for s in body.sensors]}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

@app.get('/api/onboarding/sensors/{name}')
def list_sensors(name: str, request: FastAPIRequest):
    require_network(name, request.state.owner)
    return {'network_name': name, 'source': 'simulated',
            'sensors': [s.model_dump() for s in load_sensors(name)]}

@app.get('/api/onboarding/sensors/{name}/sample')
def sample_sensors(name: str, request: FastAPIRequest):
    require_network(name, request.state.owner)
    sensors = load_sensors(name)
    if not sensors:
        raise HTTPException(422, 'Register at least one sensor first')
    try:
        rows = SimulatedSensorAdapter().read(name, sensors)
        return {'network_name': name, 'source': 'simulated', 'status': 'simulated_stream',
                'readings': rows, 'missing_readings': []}
    except Exception as exc:
        raise HTTPException(422, f'Simulated sensor sample failed: {exc}') from exc

@app.post('/api/network/upload')
async def upload_network(request: FastAPIRequest, file: UploadFile | None = File(None)):
    clean_name = None
    content = None

    if file is not None and getattr(file, 'filename', None):
        filename = file.filename
        if not filename.lower().endswith('.inp'):
            raise HTTPException(400, 'Only EPANET .inp files are accepted')
        clean_name = re.sub(r'[^a-zA-Z0-9_\-]', '', Path(filename).stem)
        content = await file.read(2 * 1024 * 1024 + 1)
    else:
        content_type = request.headers.get('content-type', '')
        if 'multipart/form-data' in content_type:
            form = await request.form()
            upload = form.get('file')
            if upload and hasattr(upload, 'filename'):
                if not upload.filename.lower().endswith('.inp'):
                    raise HTTPException(400, 'Only EPANET .inp files are accepted')
                clean_name = re.sub(r'[^a-zA-Z0-9_\-]', '', Path(upload.filename).stem)
                content = await upload.read(2 * 1024 * 1024 + 1)
        elif 'application/json' in content_type:
            body = await request.json()
            raw_name = body.get('name') or Path(body.get('filename', 'custom.inp')).stem
            clean_name = re.sub(r'[^a-zA-Z0-9_\-]', '', raw_name)
            raw_content = body.get('content', '')
            content = raw_content.encode('utf-8') if isinstance(raw_content, str) else raw_content
        else:
            raw_content = await request.body()
            if raw_content:
                clean_name = f'Custom_{int(time.time())}'
                content = raw_content

    if not clean_name:
        clean_name = f'Custom_{int(time.time())}'
    if not content:
        raise HTTPException(400, 'Empty file or content received')
    if len(content) > 2 * 1024 * 1024 or b'\x00' in content or b'[JUNCTIONS]' not in content.upper():
        raise HTTPException(422, 'Invalid or oversized EPANET file')
    if clean_name in ('Net1', 'Net2') or clean_name.startswith('H_'):
        raise HTTPException(422, 'Choose another network name')

    uploads_dir = Path(__file__).resolve().parents[1] / 'data' / 'uploads'
    uploads_dir.mkdir(parents=True, exist_ok=True)
    dest_path = uploads_dir / f'{clean_name}.inp'
    marker = uploads_dir / f'{clean_name}.owner'
    if dest_path.exists():
        raise HTTPException(409, 'A network with this name already exists')
    dest_path.write_bytes(content)

    try:
        wn = load_network(clean_name)
        cat = build_catalog(clean_name)
        marker.write_text(request.state.owner)
        series_baseline.cache_clear()
        topo = topology(clean_name)
        return {
            'network_name': clean_name,
            'junctions_count': len(wn.junction_name_list),
            'pipes_count': len(wn.pipe_name_list),
            'zones_count': len(topo.get('zones', [])),
            'topology': topo,
            'baseline': cat['baseline'],
            'networks': list_networks(request)['networks']
        }
    except Exception as exc:
        if dest_path.exists():
            dest_path.unlink()
        raise HTTPException(422, f'Failed to process .inp network: {exc}') from exc

@app.post('/api/impact')
def compute_impact(req: ComparisonRequest, request: FastAPIRequest):
    require_network(req.network_name, request.state.owner)
    if req.network_name.startswith('H_'):
        raise HTTPException(422, 'Water-loss impact needs a household model calibrated against real measurements')
    try:
        return analyze_impact(req.network_name, req.candidate)
    except Exception as exc:
        raise HTTPException(422, f'Quantitative impact calculation failed: {exc}') from exc

@app.get('/api/network/{name}')
def get_network(name: str, request: FastAPIRequest):
    require_network(name, request.state.owner)
    try:
        result = topology(name)
        if name.startswith('H_'):
            draft, validation = load_draft(name, request.state.owner)
            friendly = {item.id: item.name for item in draft.nodes + draft.links}
            for item in result['nodes'] + result['edges']:
                item['data']['name'] = friendly.get(item['data']['id'], item['data']['id'])
            result.update(display_name=draft.name, model_validation=validation)
        return result
    except ValueError as e:
        raise HTTPException(404, str(e)) from e

@app.get('/api/baseline/{name}')
def get_baseline(name: str, request: FastAPIRequest):
    require_network(name, request.state.owner)
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
def demo_series(kind: str, request: FastAPIRequest, network_name: str = 'Net1', network: str | None = None):
    net = network or network_name
    require_network(net, request.state.owner)
    try:
        wn = load_network(net)
    except Exception as e:
        raise HTTPException(404, str(e)) from e
    if net == 'Net1':
        samples = {'leak': Candidate('pipe_leak', '11', .55),
                   'valve': Candidate('valve_closure', '21', .55),
                   'surge': Candidate('demand_surge', '12', .55)}
    elif net == 'Net2':
        samples = {'leak': Candidate('pipe_leak', '11', .55),
                   'valve': Candidate('valve_closure', '7', .55),
                   'surge': Candidate('demand_surge', '12', .55)}
    else:
        p_ids = wn.pipe_name_list
        j_ids = wn.junction_name_list
        p1 = p_ids[0] if p_ids else '1'
        p2 = p_ids[1] if len(p_ids) > 1 else p1
        j1 = j_ids[0] if j_ids else '1'
        samples = {'leak': Candidate('pipe_leak', p1, .55),
                   'valve': Candidate('valve_closure', p2, .55),
                   'surge': Candidate('demand_surge', j1, .55)}
    if kind not in samples:
        raise HTTPException(404, 'Unknown demo kind')
    return {'baseline': series_baseline(net),
            'fault': simulate_series(net, samples[kind]),
            'note': 'Two independent model projections. Fault is active from hour 0; diagnosis scores hour 0.'}

@app.post('/api/compare-series')
def compare_series(req: ComparisonRequest, request: FastAPIRequest):
    require_network(req.network_name, request.state.owner)
    try:
        wn = load_network(req.network_name)
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
def demo(kind: str, request: FastAPIRequest, network_name: str = 'Net1', network: str | None = None):
    net = network or network_name
    require_network(net, request.state.owner)
    try:
        wn = load_network(net)
    except Exception as e:
        raise HTTPException(404, str(e)) from e
    if net == 'Net1':
        samples = {'leak': Candidate('pipe_leak', '11', 0.55),
                   'valve': Candidate('valve_closure', '21', 0.55),
                   'surge': Candidate('demand_surge', '12', 0.55)}
    elif net == 'Net2':
        samples = {'leak': Candidate('pipe_leak', '11', 0.55),
                   'valve': Candidate('valve_closure', '7', 0.55),
                   'surge': Candidate('demand_surge', '12', 0.55)}
    else:
        p_ids = wn.pipe_name_list
        j_ids = wn.junction_name_list
        p1 = p_ids[0] if p_ids else '1'
        p2 = p_ids[1] if len(p_ids) > 1 else p1
        j1 = j_ids[0] if j_ids else '1'
        samples = {'leak': Candidate('pipe_leak', p1, 0.55),
                   'valve': Candidate('valve_closure', p2, 0.55),
                   'surge': Candidate('demand_surge', j1, 0.55)}
    if kind not in samples:
        raise HTTPException(404, 'Unknown demo kind')
    try:
        obs = simulate(net, samples[kind])
    except Exception as e:
        raise HTTPException(500, f'Demo simulation failed: {e}') from e
    nodes = list(obs['pressures'])
    pipes = list(obs['flows'])
    return {'network_name': net, 'observed': {'pressures': {n: obs['pressures'][n] for n in nodes},
                                              'flows': {p: obs['flows'][p] for p in pipes}},
            'ground_truth': vars(samples[kind])}

@app.post('/api/demo/custom')
def custom_demo(req: CustomFault, request: FastAPIRequest):
    require_network(req.network_name, request.state.owner)
    wn = load_network(req.network_name)
    allowed = wn.junction_name_list if req.fault_type == 'demand_surge' else wn.pipe_name_list
    if req.location_id not in allowed:
        raise HTTPException(422, 'Select a valid junction for demand or a valid pipe for leak/restriction')
    candidate = Candidate(req.fault_type, req.location_id, req.severity)
    try:
        observed = simulate(req.network_name, candidate)
        series = {'baseline': series_baseline(req.network_name), 'fault': simulate_series(req.network_name, candidate),
                  'note': 'Independent healthy and fault projections; diagnosis scores hour 0.'}
    except Exception as e:
        raise HTTPException(422, f'Could not simulate selected fault: {e}') from e
    return {'network_name': req.network_name, 'observed': observed,
            'ground_truth': vars(candidate), 'series': series}

@app.post('/api/investigate')
def investigate(req: Request, request: FastAPIRequest):
    validate(req, request.state.owner)
    try:
        state = trace_workflow(lambda: GRAPH.invoke({'network_name': req.network_name,
            'observed': req.observed.model_dump(), 'budget': req.budget,
            'rows': [], 'pending': [], 'events': []},
            config={'recursion_limit': 50, 'run_name': 'WaterGuard investigation',
                    'tags': ['waterguard', req.network_name]}))
        return enrich_report(state['report'], req, request.state.owner,
                             audit_history=state.get('events', []))
    except Exception as e:
        raise HTTPException(500, f'Investigation failed: {e}') from e

def enrich_report(report, req: Request, owner: str, audit_history=None):
    validation = None
    if req.network_name.startswith('H_'):
        _, validation = load_draft(req.network_name, owner)
    report.update(observation_source=req.observation_source, measured_at=req.measured_at,
                  sensor_quality=req.sensor_quality, operating_conditions=req.operating_conditions, model_validation=validation,
                  model_fingerprint=load_catalog(req.network_name).get('model_fingerprint'),
                  observed=req.observed.model_dump(), audit_history=audit_history or [],
                  baseline_limitation='Healthy comparison is one hour-zero model snapshot; demand and tank conditions may change over time.',
                  diagnostic_status=('no_supported_fault_fit' if report.get('ranked_faults') and report['ranked_faults'][0]['score'] > .25 else
                                     'insufficient_evidence' if report.get('ambiguous') else 'hypothesis'))
    if validation:
        report['diagnostic_status'] = 'exploratory_only' if validation.get('estimated') else report['diagnostic_status']
        report['quantitative_impact'] = None
    return report

@app.post('/api/investigations', status_code=202)
async def create_session(req: Request, request: FastAPIRequest):
    validate(req, request.state.owner)
    sid = str(uuid4())
    owner = request.state.owner
    SESSIONS[sid] = {'events': [], 'status': 'running', 'result': None,
                     'owner': owner, 'network_name': req.network_name}
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
                    elif node == 'rank_zones':
                        event['zone_rankings'] = delta.get('zone_rankings', [])
                    elif node == 'expand':
                        event['expanded_zone_ids'] = delta.get('expanded_zone_ids', [])
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
                        SESSIONS[sid]['result'] = enrich_report(delta['report'], req, owner)
        try:
            trace_workflow(execute_graph)
            SESSIONS[sid]['status'] = 'complete'
            SESSIONS[sid]['events'].append({'type': 'complete'})
            if SESSIONS[sid]['result'] is not None:
                SESSIONS[sid]['result']['audit_history'] = list(SESSIONS[sid]['events'])
            HISTORY_DIR.mkdir(parents=True, exist_ok=True)
            (HISTORY_DIR / f'{sid}.json').write_text(json.dumps(SESSIONS[sid]))
        except Exception as e:
            SESSIONS[sid]['status'] = 'error'
            SESSIONS[sid]['events'].append({'type': 'error', 'message': str(e)})
            HISTORY_DIR.mkdir(parents=True, exist_ok=True)
            (HISTORY_DIR / f'{sid}.json').write_text(json.dumps(SESSIONS[sid]))
    POOL.submit(run)
    return {'session_id': sid}

@app.get('/api/investigations/{sid}')
def get_session(sid: str, request: FastAPIRequest):
    if sid not in SESSIONS and (HISTORY_DIR / f'{sid}.json').exists():
        SESSIONS[sid] = json.loads((HISTORY_DIR / f'{sid}.json').read_text())
    if sid not in SESSIONS or SESSIONS[sid]['owner'] != request.state.owner:
        raise HTTPException(404, 'Session not found')
    d = SESSIONS[sid]
    return {'status': d['status'], 'result': d['result']}

@app.get('/api/investigations/{sid}/events')
async def events(sid: str, request: FastAPIRequest):
    if sid not in SESSIONS and (HISTORY_DIR / f'{sid}.json').exists():
        SESSIONS[sid] = json.loads((HISTORY_DIR / f'{sid}.json').read_text())
    if sid not in SESSIONS or SESSIONS[sid]['owner'] != request.state.owner:
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
