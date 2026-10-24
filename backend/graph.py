"""LangGraph: offline retrieval, bounded targeted WNTR verification, evidence."""
from __future__ import annotations
from typing import Optional, TypedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import math
import time
from langgraph.graph import StateGraph, START, END
from .narrative import narrative
from .observability import instrument
from .fingerprints import load_catalog, shortlist, estimate_severity
from .models import Candidate
from .hydraulics import simulate, isolation_impact
from .scoring import score, explain
from .context import fetch_context
from .decisions import recommend

class State(TypedDict, total=False):
    network_name: str
    observed: dict
    budget: int
    catalog: dict
    baseline: dict
    baseline_score: float
    alert: dict
    context: dict
    pending: list[dict]
    rows: list[dict]
    diagnosis: dict
    recommendation: dict
    sensor_recommendation: Optional[dict]
    report: dict
    events: list[dict]
    started: float


def monitor(s: State):
    catalog = load_catalog(s['network_name'])
    baseline = catalog['baseline']
    baseline_score = score(s['observed'], baseline)
    return {'catalog': catalog, 'baseline': baseline,
            'baseline_score': baseline_score,
            'alert': {'anomalous': baseline_score > 0.02,
                      'standardised_residual': baseline_score},
            'started': time.monotonic()}


def context_agent(s: State):
    return {'context': fetch_context(s['network_name'])}


def hypotheses(s: State):
    if not s['alert']['anomalous']:
        return {'pending': []}
    # Rank all 99 precomputed signatures with no new hydraulic runs.
    ranked = shortlist(s['catalog'], s['observed'], limit=s['budget'])
    pending = []
    for item in ranked:
        candidate = dict(item['candidate'])
        candidate['severity'] = estimate_severity(s['catalog'], s['observed'], item)
        pending.append({'candidate': candidate,
                        'fingerprint_score': item['fingerprint_score']})
    return {'pending': pending}


def worker(name, observed, item):
    h = Candidate(**item['candidate'])
    try:
        predicted = simulate(name, h)
        return {'candidate': asdict(h), 'score': score(observed, predicted),
                'fingerprint_score': item['fingerprint_score'],
                'predicted': predicted, 'status': 'ok'}
    except Exception as e:
        return {'candidate': asdict(h), 'status': 'failed', 'error': str(e)}


def dispatcher(s: State):
    batch, pending = s['pending'][:2], s['pending'][2:]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, s['network_name'], s['observed'], item)
                   for item in batch]
        new_rows = [future.result() for future in futures]
    rows = s['rows'] + new_rows
    return {'rows': rows, 'pending': pending,
            'events': s['events'] + [{'type': 'simulation', 'count': len(rows),
                         'tested': [{k: r.get(k) for k in ('candidate', 'score', 'status')}
                                    for r in new_rows]}]}


def assess(s: State):
    valid = sorted((r for r in s['rows'] if r.get('status') == 'ok'),
                   key=lambda r: r['score'])
    if not valid:
        return {'ranked': [], 'ambiguous': True, 'reason': 'All verification simulations failed'}
    weights = [math.exp(-min(r['score'], 1000)/2) for r in valid]
    total = sum(weights)
    ranked = [{**r, 'relative_weight': weights[i]/total if total else 0.,
               'rank': i+1} for i, r in enumerate(valid)]
    gap = ranked[1]['score']-ranked[0]['score'] if len(ranked)>1 else 0.
    fit = ranked[0]['score']
    ambiguous = (len(ranked)<2 or fit >= s['baseline_score'] or fit > .25
                 or gap < max(.02, fit * .5))
    return {'ranked': ranked, 'ambiguous': ambiguous,
            'reason': ('Scenarios overlap or the model fits poorly; obtain independent readings'
                       if ambiguous else 'Best tested type and location have a distinct fit')}


def evidence(s: State):
    return {'diagnosis': assess(s)}


def route(s: State):
    ranked = s['diagnosis']['ranked']
    if len(s['rows']) >= s['budget'] or not s['pending']:
        return 'diagnose'
    if len(s['rows']) >= 4 and not s['diagnosis']['ambiguous'] and ranked:
        # Only stop if the next precomputed competitor also lies well behind.
        if s['pending'][0]['fingerprint_score'] > ranked[0]['score'] + .25:
            return 'diagnose'
    return 'simulate'


def diagnosis_agent(s: State):
    result = s['diagnosis']
    ranked = []
    for r in result['ranked'][:5]:
        readings = []
        for kind in ('pressures', 'flows'):
            for sensor_id, observed_value in s['observed'].get(kind, {}).items():
                sigma = .8 if kind == 'pressures' else .001
                readings.append({
                    'kind': 'pressure' if kind == 'pressures' else 'flow',
                    'sensor_id': sensor_id,
                    'observed': observed_value,
                    'baseline': s['baseline'][kind][sensor_id],
                    'predicted': r['predicted'][kind][sensor_id],
                    'normalised_error': abs(observed_value-r['predicted'][kind][sensor_id])/sigma
                })
        readings.sort(key=lambda x: abs(x['observed']-x['baseline'])/
                      (.8 if x['kind']=='pressure' else .001), reverse=True)
        ranked.append({k:r[k] for k in ('candidate','score','relative_weight','rank')}
                      | {'explanation': narrative(explain(r,s['observed'],s['baseline']),
                                                 r['candidate']),
                         'evidence': readings})
    rec = recommend(result, s['context'])
    if not result['ambiguous'] and ranked and ranked[0]['candidate']['fault_type'] != 'demand_surge':
        rec['isolation_what_if'] = isolation_impact(s['network_name'],ranked[0]['candidate']['location_id'])
    return {'diagnosis': {**result,'ranked':ranked}, 'recommendation':rec}


def sensor_planner(s: State):
    if not s['diagnosis']['ambiguous']:
        return {'sensor_recommendation': None}
    ranked = sorted((r for r in s['rows'] if r['status']=='ok'),key=lambda r:r['score'])
    if len(ranked)<2:
        return {'sensor_recommendation': None}
    a,b=ranked[:2]
    observed=set(s['observed'].get('pressures',{}))
    differences=[(abs(v-b['predicted']['pressures'][n]),n)
                 for n,v in a['predicted']['pressures'].items() if n not in observed]
    if not differences:
        return {'sensor_recommendation': None}
    diff,node=max(differences)
    return {'sensor_recommendation':{'junction_id':node,
             'predicted_difference_m':round(diff,3),
             'note':'Suggested next measurement for the two best verified scenarios.'}}


def report_agent(s: State):
    d=s['diagnosis']
    return {'report': {'network_name':s['network_name'],'alert':s['alert'],
            'ranked_faults':d['ranked'],'ambiguous':d['ambiguous'],
            'reason':d['reason'],'simulation_count':len(s['rows']),
            'offline_simulation_count':s['catalog']['offline_simulation_count'],
            'baseline_simulations':0,'isolation_simulations':int('isolation_what_if' in s['recommendation']),
            'candidate_space_size':len(s['catalog']['signatures']),
            'recommendation':s['recommendation'],
            'sensor_recommendation':s['sensor_recommendation'],'context':s['context'],
            'elapsed_ms':round((time.monotonic()-s['started'])*1000)}}


def build_graph():
    g=StateGraph(State)
    for name,fn in [('monitor',monitor),('context',context_agent),
                    ('hypotheses',hypotheses),('simulate',dispatcher),
                    ('evidence',evidence),('diagnose',diagnosis_agent),
                    ('sensor_planner',sensor_planner),('report',report_agent)]:
        g.add_node(name,instrument(name,fn))
    g.add_edge(START,'monitor')
    g.add_edge('monitor','context')
    g.add_edge('context','hypotheses')
    g.add_node('no_alert',lambda s:{'diagnosis':{'ranked':[],'ambiguous':True,
                'reason':'No anomaly relative to model baseline'},
                'recommendation':{'action':'MONITOR','text':'No anomaly detected'}})
    g.add_conditional_edges('hypotheses',lambda s:'simulate' if s['pending'] else 'no_alert',
                             {'simulate':'simulate','no_alert':'no_alert'})
    g.add_edge('no_alert','sensor_planner')
    g.add_edge('simulate','evidence')
    g.add_conditional_edges('evidence',route,{'simulate':'simulate','diagnose':'diagnose'})
    g.add_edge('diagnose','sensor_planner')
    g.add_edge('sensor_planner','report')
    g.add_edge('report',END)
    return g.compile()

GRAPH=build_graph()

def investigate(name,observed,budget=8):
    return GRAPH.invoke({'network_name':name,'observed':observed,'budget':budget,
                         'pending':[],'rows':[],'events':[]},
                        config={'recursion_limit':50})
