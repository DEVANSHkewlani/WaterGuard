"""Network topology, hydraulic fault injection and independent WNTR runs."""
from __future__ import annotations
from .zones import zones_for
import math
import wntr
from .models import Candidate, SEVERITIES

from pathlib import Path
import networkx as nx

DATA_DIR = Path(__file__).resolve().parents[1] / 'data'
UPLOADS_DIR = DATA_DIR / 'uploads'
LEAK_AREA_M2 = 0.00035
CLOSURE_LOSS_FACTOR = 300
SURGE_DEMAND_M3S = 0.012

def available_networks() -> list[str]:
    nets = ['Net2', 'Net1']
    if UPLOADS_DIR.exists():
        for f in sorted(UPLOADS_DIR.glob('*.inp')):
            if f.stem not in nets:
                nets.append(f.stem)
    return nets

def network(name='Net1'):
    if name in ('Net1', 'Net2'):
        wn = wntr.network.WaterNetworkModel(name)
    else:
        inp_file = UPLOADS_DIR / f'{name}.inp'
        if not inp_file.exists():
            inp_file = DATA_DIR / f'{name}.inp'
        if not inp_file.exists():
            raise ValueError(f'Unsupported network: {name}. Upload {name}.inp first.')
        wn = wntr.network.WaterNetworkModel(str(inp_file))

    # Auto-generate coordinates if missing in custom network
    coords = [node.coordinates for _, node in wn.nodes()]
    if not coords or all(c == (0.0, 0.0) or not c for c in coords):
        G = wn.to_graph()
        pos = nx.spring_layout(G, seed=42)
        for n_id, p in pos.items():
            wn.get_node(n_id).coordinates = (float(p[0]) * 100.0, float(p[1]) * 100.0)

    wn.options.time.duration = 0  # one hydraulic snapshot; never average over a day
    return wn


def topology(name='Net1'):
    wn = network(name)
    nodes = []
    for node_id, node in wn.nodes():
        x, y = node.coordinates
        nodes.append({'data': {'id': node_id, 'kind': node.node_type}, 'position': {'x': x, 'y': -y}})
    edges = [{'data': {'id': link_id, 'source': link.start_node_name,
                       'target': link.end_node_name, 'kind': link.link_type}}
             for link_id, link in wn.links()]
    return {'nodes': nodes, 'edges': edges, 'junctions': list(wn.junction_name_list),
            'pipes': list(wn.pipe_name_list), 'zones': zones_for(wn)}


def apply_fault(wn, h: Candidate):
    if h.fault_type == 'pipe_leak':
        if h.location_id not in wn.pipe_name_list:
            raise ValueError('Leak location must be a pipe')
        pipe = wn.get_link(h.location_id)
        wntr.morph.split_pipe(wn, h.location_id, '_leak_segment', '_leak_node', return_copy=False)
        node = wn.get_node('_leak_node')
        node.add_leak(wn, area=LEAK_AREA_M2 * h.severity, start_time=0)
    elif h.fault_type == 'valve_closure':
        if h.location_id not in wn.pipe_name_list:
            raise ValueError('Closure location must be a pipe')
        pipe = wn.get_link(h.location_id)
        pipe.minor_loss += CLOSURE_LOSS_FACTOR * h.severity / max(1.01 - h.severity, 0.01)
    elif h.fault_type == 'demand_surge':
        if h.location_id not in wn.junction_name_list:
            raise ValueError('Surge location must be a junction')
        node = wn.get_node(h.location_id)
        node.add_demand(base=SURGE_DEMAND_M3S * h.severity, pattern_name=None, category='surge')
    else:
        raise ValueError('Unknown fault type')


def simulate(name='Net1', candidate: Candidate | None = None):
    wn = network(name)  # distinct mutable model per run, safe for parallel workers
    original_nodes = list(wn.junction_name_list)
    original_pipes = list(wn.pipe_name_list)
    if candidate:
        apply_fault(wn, candidate)
    result = wntr.sim.WNTRSimulator(wn).run_sim(convergence_error=True)
    p = result.node['pressure'].iloc[0]
    q = result.link['flowrate'].iloc[0]
    return {'pressures': {n: float(p[n]) for n in original_nodes},
            'flows': {e: float(q[e]) for e in original_pipes}}


def all_candidates(name='Net1'):
    wn = network(name)
    return [Candidate(t, loc, sev)
            for t, locations in (('pipe_leak', wn.pipe_name_list),
                                 ('valve_closure', wn.pipe_name_list),
                                 ('demand_surge', wn.junction_name_list))
            for loc in locations for sev in SEVERITIES]


def score(observed, predicted, pressure_sigma=0.8, flow_sigma=0.001):
    """Mean squared standardised residual; flow inputs use m³/s."""
    terms = []
    for kind, sigma in (('pressures', pressure_sigma), ('flows', flow_sigma)):
        for key, value in observed.get(kind, {}).items():
            if key not in predicted[kind]:
                raise ValueError(f'Unknown sensor {key}')
            terms.append(((value - predicted[kind][key]) / sigma) ** 2)
    if not terms:
        raise ValueError('At least one reading is required')
    return sum(terms) / len(terms)

def isolation_impact(name, pipe_id, minimum_pressure_m=14.0):
    """What-if only. A pipe may have no operable isolation valve in reality."""
    wn = network(name)
    if pipe_id not in wn.pipe_name_list:
        raise ValueError('Unknown pipe')
    wn.get_link(pipe_id).initial_status = wntr.network.LinkStatus.Closed
    try:
        result = wntr.sim.WNTRSimulator(wn).run_sim(convergence_error=True)
        pressures = result.node['pressure'].iloc[0]
        affected = [n for n in wn.junction_name_list
                    if not math.isfinite(float(pressures[n])) or pressures[n] < minimum_pressure_m]
        return {'simulated_safe': not affected, 'affected_junctions': affected,
                'minimum_pressure_m': min(float(pressures[n]) for n in wn.junction_name_list),
                'assumption': 'Candidate pipe is closable; field valve layout is unknown.'}
    except Exception as e:
        return {'simulated_safe': False, 'affected_junctions': [], 'error': str(e),
                'assumption': 'Simulation failed; isolation safety is unknown.'}


def simulate_series(name='Net1', candidate: Candidate | None = None, hours=12):
    """Independent extended-period projection for visual explanation.

    Faults are active from hour zero. Diagnosis still uses the initial snapshot.
    """
    if not 1 <= hours <= 24:
        raise ValueError('hours must be between 1 and 24')
    wn = network(name)
    original_nodes = list(wn.junction_name_list)
    original_pipes = list(wn.pipe_name_list)
    wn.options.time.duration = hours * 3600
    wn.options.time.hydraulic_timestep = 3600
    wn.options.time.report_timestep = 3600
    if candidate:
        apply_fault(wn, candidate)
    result = wntr.sim.WNTRSimulator(wn).run_sim(convergence_error=True)
    times = [int(t / 3600) for t in result.node['pressure'].index]
    return {'hours': times,
            'pressures': {n: [float(v) for v in result.node['pressure'][n]]
                          for n in original_nodes},
            'flows': {p: [float(v) for v in result.link['flowrate'][p]]
                      for p in original_pipes}}
