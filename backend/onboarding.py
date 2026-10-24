"""Household draft, conservative validation, and WNTR conversion."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4
import json
import re
import subprocess
import shutil

import networkx as nx
import wntr
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field

from .hydraulics import UPLOADS_DIR

ID = re.compile(r'^[A-Za-z][A-Za-z0-9_]{0,30}$')
MAX_UPLOAD = 10 * 1024 * 1024
MAX_PAGES = 5
DRAFT_DIR = UPLOADS_DIR / 'drafts'
DEMO_DEFAULTS = {'head_m': 30., 'elevation_m': 0., 'demand_lps_outlet': .1,
                 'demand_lps_junction': 0., 'tank_level_m': 3., 'tank_diameter_m': 2.,
                 'length_m': 10., 'diameter_mm': 20., 'pump_power_kw': .5,
                 'valve_setting': 0., 'roughness': 110.}
MATERIAL_ROUGHNESS = {'pvc': 150., 'copper': 140., 'galvanized': 120., 'cast iron': 100.}


class Place(BaseModel):
    id: str
    name: str
    kind: str  # source, tank, junction, outlet
    x: float = 100
    y: float = 100
    elevation_m: float | None = None
    demand_lps: float | None = None
    head_m: float | None = None
    tank_level_m: float | None = None
    tank_diameter_m: float | None = None
    provenance: dict[str, str] = Field(default_factory=dict)


class Connection(BaseModel):
    id: str
    name: str
    kind: str = 'pipe'  # pipe, pump, valve
    start: str
    end: str
    length_m: float | None = None
    diameter_mm: float | None = None
    material: str | None = None
    pump_power_kw: float | None = None
    valve_setting: float | None = None
    provenance: dict[str, str] = Field(default_factory=dict)


class Draft(BaseModel):
    id: str = Field(default_factory=lambda: 'H_' + uuid4().hex[:16])
    name: str = 'My water network'
    origin: str = 'manual'
    nodes: list[Place] = Field(default_factory=list)
    links: list[Connection] = Field(default_factory=list)
    issue: str = ''
    document: dict = Field(default_factory=dict)


def new_draft(origin='manual', name='My water network'):
    return Draft(name=name[:80], origin=origin)


def guided_draft(answers: dict):
    source = str(answers.get('source') or 'municipal')
    outlets = [s.strip()[:60] for s in str(answers.get('outlets') or '').split(',') if s.strip()][:12]
    draft = new_draft('guided', str(answers.get('name') or 'My water network'))
    draft.document = {'floors': str(answers.get('floors') or 'unknown')[:20],
                      'sensor_notes': str(answers.get('sensors') or '')[:500],
                      'interpretation': 'Connections proposed from answers; homeowner confirmation required'}
    if not outlets:
        return draft
    source_kind = 'tank' if source in ('underground tank', 'overhead tank') else 'source'
    draft.nodes = [Place(id='Source', name=source.title(), kind=source_kind, x=80, y=170,
                         provenance={'name': 'user'}),
                   Place(id='Branch', name='Main branch', kind='junction', x=350, y=170,
                         provenance={'name': 'inferred'})]
    if answers.get('pump'):
        draft.links.append(Connection(id='Pump', name='Supply pump', kind='pump',
                           start='Source', end='Branch', provenance={'kind': 'user', 'start': 'inferred', 'end': 'inferred'}))
    else:
        draft.links.append(Connection(id='Main', name='Main supply pipe', start='Source', end='Branch',
                           provenance={'start': 'inferred', 'end': 'inferred'}))
    for i, outlet in enumerate(outlets, 1):
        draft.nodes.append(Place(id=f'Outlet{i}', name=outlet, kind='outlet', x=620,
                                 y=80 + i * 90, provenance={'name': 'user'}))
        draft.links.append(Connection(id=f'Pipe{i}', name=f'Pipe to {outlet}', start='Branch',
                                      end=f'Outlet{i}', provenance={'start': 'inferred', 'end': 'inferred'}))
    draft.issue = str(answers.get('issue') or '')[:500]
    first_pipe = next((link for link in draft.links if link.kind == 'pipe'), None)
    for field, target in (('length_m', first_pipe), ('head_m', draft.nodes[0] if source_kind == 'source' else None)):
        raw = answers.get(field)
        if target is not None and raw is not None and raw != '':
            setattr(target, field, float(raw))
            target.provenance[field] = 'user'
    diameter = answers.get('diameter_mm')
    if diameter is not None and diameter != '':
        for link in draft.links:
            if link.kind == 'pipe':
                link.diameter_mm = float(diameter)
                link.provenance['diameter_mm'] = 'user'
    return draft


def inspect_document(filename: str, content: bytes):
    if not content or len(content) > MAX_UPLOAD:
        raise ValueError('Choose a nonempty file smaller than 10 MB')
    suffix = Path(filename).suffix.lower()
    if suffix not in ('.pdf', '.png', '.jpg', '.jpeg'):
        raise ValueError('Choose a PDF, PNG, or JPG diagram')
    if suffix == '.pdf':
        if not content.startswith(b'%PDF-'):
            raise ValueError('The PDF file signature is invalid')
        with NamedTemporaryFile(suffix='.pdf') as temp:
            temp.write(content)
            temp.flush()
            page_count = None
            if shutil.which('pdfinfo'):
                try:
                    result = subprocess.run(['pdfinfo', temp.name], capture_output=True, text=True,
                                            timeout=10, check=True)
                    pages = re.search(r'^Pages:\s*(\d+)', result.stdout, re.MULTILINE)
                    if pages:
                        page_count = int(pages.group(1))
                except Exception:
                    pass
            if page_count is None:
                # Pure Python fallback: count /Type /Page references in the PDF stream
                matches = re.findall(rb'/Type\s*/Page\b', content)
                page_count = len(matches)
            if not 1 <= page_count <= MAX_PAGES:
                raise ValueError(f'PDF must contain 1–{MAX_PAGES} readable pages')
            info = {'type': 'pdf', 'pages': page_count, 'filename': Path(filename).name[:100]}
            extractor = shutil.which('pdftotext')
            if not extractor and shutil.which('pdfinfo'):
                # The bundled Poppler runtime may expose pdfinfo but omit a PATH shim.
                info_bin = Path(shutil.which('pdfinfo'))
                for candidate in (info_bin.with_name('pdftotext'),
                                  (info_bin.parent / '../../native/poppler/bin/pdftotext').resolve()):
                    if candidate.is_file() and candidate.stat().st_mode & 0o111:
                        extractor = str(candidate)
                        break
            if extractor:
                try:
                    extracted = subprocess.run([extractor, '-layout', temp.name, '-'],
                                               capture_output=True, text=True, timeout=15, check=True)
                    info['text_excerpt'] = extracted.stdout.strip()[:3000]
                except (OSError, subprocess.SubprocessError):
                    info['text_excerpt'] = ''
            else:
                info['text_excerpt'] = ''
            return info
    try:
        with Image.open(BytesIO(content)) as img:
            img.verify()
        with Image.open(BytesIO(content)) as img:
            if img.width * img.height > 25_000_000:
                raise ValueError('Image has too many pixels')
            kind = 'png' if img.format == 'PNG' else 'jpeg' if img.format == 'JPEG' else ''
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError('Could not read this image') from exc
    if kind != ('png' if suffix == '.png' else 'jpeg'):
        raise ValueError('Image contents do not match its file type')
    return {'type': kind, 'pages': 1, 'filename': Path(filename).name[:100]}


def validate_draft(draft: Draft, estimated=False):
    errors, assumptions, assumed_values = [], [], []
    if not draft.nodes or not draft.links:
        errors.append('Add a water source, at least one outlet, and their connections')
    if len(draft.nodes) > 40 or len(draft.links) > 50:
        errors.append('This editor supports at most 40 points and 50 connections')
    ids = [n.id for n in draft.nodes] + [e.id for e in draft.links]
    if any(not ID.fullmatch(i) for i in ids) or len(ids) != len(set(ids)):
        errors.append('Every point and connection needs a unique stable ID using letters, numbers, or underscores')
    node_ids = {n.id for n in draft.nodes}
    sources = {n.id for n in draft.nodes if n.kind in ('source', 'tank')}
    if not sources:
        errors.append('Add a water source or tank')
    if not any(n.kind in ('junction', 'outlet') for n in draft.nodes):
        errors.append('Add at least one branch or outlet')
    graph = nx.Graph()
    graph.add_nodes_from(node_ids)
    for n in draft.nodes:
        if n.kind not in ('source', 'tank', 'junction', 'outlet'):
            errors.append(f'{n.name}: unsupported point type')
        if n.kind == 'source' and n.head_m is None:
            (assumptions if estimated else errors).append(f'{n.name}: supply pressure/head is unknown')
            if estimated: assumed_values.append({'element': n.id, 'field': 'head_m', 'value': DEMO_DEFAULTS['head_m']})
        if n.kind == 'tank' and (n.elevation_m is None or n.tank_level_m is None or n.tank_diameter_m is None):
            (assumptions if estimated else errors).append(f'{n.name}: tank elevation, level, or diameter is unknown')
        if n.kind in ('junction', 'outlet') and n.demand_lps is None:
            (assumptions if estimated else errors).append(f'{n.name}: water demand is unknown')
            if estimated: assumed_values.append({'element': n.id, 'field': 'demand_lps', 'value': DEMO_DEFAULTS['demand_lps_outlet' if n.kind == 'outlet' else 'demand_lps_junction']})
        if n.elevation_m is None and n.kind != 'source':
            (assumptions if estimated else errors).append(f'{n.name}: elevation is unknown')
        if estimated:
            for field in ('elevation_m', 'tank_level_m', 'tank_diameter_m'):
                if getattr(n, field) is None and (field == 'elevation_m' and n.kind != 'source' or field.startswith('tank_') and n.kind == 'tank'):
                    assumed_values.append({'element': n.id, 'field': field, 'value': DEMO_DEFAULTS[field]})
        if n.demand_lps is not None and n.demand_lps < 0:
            errors.append(f'{n.name}: demand cannot be negative')
    for e in draft.links:
        if e.kind not in ('pipe', 'pump', 'valve'):
            errors.append(f'{e.name}: unsupported connection type')
        if e.start not in node_ids or e.end not in node_ids or e.start == e.end:
            errors.append(f'{e.name}: choose two different existing endpoints')
        else:
            graph.add_edge(e.start, e.end)
        needed = ('length_m', 'diameter_mm') if e.kind == 'pipe' else ('pump_power_kw',) if e.kind == 'pump' else ('diameter_mm', 'valve_setting')
        for field in needed:
            value = getattr(e, field)
            if value is None:
                (assumptions if estimated else errors).append(f'{e.name}: {field.replace("_", " ")} is unknown')
                if estimated: assumed_values.append({'element': e.id, 'field': field, 'value': DEMO_DEFAULTS[field]})
            elif value <= 0 and field != 'valve_setting':
                errors.append(f'{e.name}: {field.replace("_", " ")} must be positive')
    if sources and graph.number_of_nodes():
        reached = set().union(*(nx.node_connected_component(graph, src) for src in sources))
        if reached != node_ids:
            errors.append('Some points are disconnected from the water supply: ' + ', '.join(sorted(node_ids - reached)))
    if not any(e.kind == 'pipe' for e in draft.links):
        errors.append('Add at least one pipe to support fault testing')
    for e in draft.links:
        if e.kind == 'pipe':
            roughness = MATERIAL_ROUGHNESS.get((e.material or '').lower().strip(), DEMO_DEFAULTS['roughness'])
            assumed_values.append({'element': e.id, 'field': 'roughness', 'value': roughness,
                                   'note': 'Generic material estimate, not a measured pipe condition'})
    return {'structural_valid': not errors, 'errors': list(dict.fromkeys(errors)),
            'assumptions': list(dict.fromkeys(assumptions)), 'assumed_values': assumed_values,
            'estimated': estimated}


def to_wntr(draft: Draft, estimated=False):
    check = validate_draft(draft, estimated)
    if check['errors']:
        raise ValueError('; '.join(check['errors']))
    wn = wntr.network.WaterNetworkModel()
    for n in draft.nodes:
        pos = (float(n.x), -float(n.y))
        if n.kind == 'source':
            wn.add_reservoir(n.id, base_head=n.head_m if n.head_m is not None else DEMO_DEFAULTS['head_m'], coordinates=pos)
        elif n.kind == 'tank':
            wn.add_tank(n.id, elevation=n.elevation_m if n.elevation_m is not None else DEMO_DEFAULTS['elevation_m'],
                        init_level=n.tank_level_m if n.tank_level_m is not None else DEMO_DEFAULTS['tank_level_m'],
                        min_level=0., max_level=max(6., (n.tank_level_m or 3.) + 1.),
                        diameter=n.tank_diameter_m if n.tank_diameter_m is not None else DEMO_DEFAULTS['tank_diameter_m'], coordinates=pos)
        else:
            wn.add_junction(n.id, base_demand=(n.demand_lps if n.demand_lps is not None else DEMO_DEFAULTS['demand_lps_outlet' if n.kind == 'outlet' else 'demand_lps_junction']) / 1000,
                            elevation=n.elevation_m if n.elevation_m is not None else DEMO_DEFAULTS['elevation_m'], coordinates=pos)
    for e in draft.links:
        if e.kind == 'pipe':
            wn.add_pipe(e.id, e.start, e.end, length=e.length_m if e.length_m is not None else DEMO_DEFAULTS['length_m'],
                        diameter=(e.diameter_mm if e.diameter_mm is not None else DEMO_DEFAULTS['diameter_mm']) / 1000,
                        roughness=MATERIAL_ROUGHNESS.get((e.material or '').lower().strip(), DEMO_DEFAULTS['roughness']))
        elif e.kind == 'pump':
            wn.add_pump(e.id, e.start, e.end, pump_type='POWER',
                        pump_parameter=(e.pump_power_kw if e.pump_power_kw is not None else DEMO_DEFAULTS['pump_power_kw']) * 1000)
        else:
            wn.add_valve(e.id, e.start, e.end,
                         diameter=(e.diameter_mm if e.diameter_mm is not None else DEMO_DEFAULTS['diameter_mm']) / 1000,
                         valve_type='TCV', initial_setting=e.valve_setting if e.valve_setting is not None else 0.)
    wn.options.time.duration = 0
    return wn, check


def hydraulic_check(draft: Draft, estimated=False):
    check = validate_draft(draft, estimated)
    if check['errors']:
        return {**check, 'hydraulic_valid': False, 'status': 'invalid'}
    wn, check = to_wntr(draft, estimated)
    try:
        result = wntr.sim.WNTRSimulator(wn).run_sim(convergence_error=True)
        pressures = result.node['pressure'].iloc[0]
        if not all(float(pressures[n]) >= 0 for n in wn.junction_name_list):
            raise ValueError('One or more outlets has negative modeled pressure')
        check['hydraulic_valid'] = True
        check['status'] = 'estimated_demo' if estimated else 'structural_and_hydraulic'
    except Exception as exc:
        check['hydraulic_valid'] = False
        check['status'] = 'structural_only'
        check['errors'].append(f'Hydraulic run failed: {exc}')
    return check


def draft_path(name):
    if not ID.fullmatch(name) or not name.startswith('H_'):
        raise ValueError('Invalid household network ID')
    return DRAFT_DIR / f'{name}.json'


def save_draft(draft: Draft, owner: str, validation=None):
    DRAFT_DIR.mkdir(parents=True, exist_ok=True)
    draft_path(draft.id).write_text(json.dumps({'owner': owner, 'draft': draft.model_dump(),
                                                'validation': validation}))


def load_draft(name, owner):
    path = draft_path(name)
    if not path.exists():
        raise FileNotFoundError('Household network not found')
    data = json.loads(path.read_text())
    if data['owner'] != owner:
        raise PermissionError('Household network belongs to another browser')
    return Draft.model_validate(data['draft']), data.get('validation')
