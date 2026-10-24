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
from .scoring import score, explain
from .context import fetch_context
from .decisions import recommend
from .hydraulics import simulate, isolation_impact, network
from .zones import zones_for, zone_for_candidate

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
    zones: list[dict]
    zone_rankings: list[dict]
    selected_zone_ids: list[str]
    expanded_zone_ids: list[str]
    reserve: list[dict]


def monitor(s: State):
    catalog = load_catalog(s['network_name'])
    baseline = catalog['baseline']
    baseline_score = score(s['observed'], baseline)
    sensor_count = (
        len(s['observed'].get('pressures', {}))
        + len(s['observed'].get('flows', {}))
    )
    terms = []
    max_p_diff = 0.0
    max_f_diff = 0.0
    for n, p in s['observed'].get('pressures', {}).items():
        if n in baseline.get('pressures', {}):
            d = abs(p - baseline['pressures'][n])
            if d > max_p_diff:
                max_p_diff = d
            terms.append((d / 0.8) ** 2)
    for p_id, f in s['observed'].get('flows', {}).items():
        if p_id in baseline.get('flows', {}):
            d = abs(f - baseline['flows'][p_id])
            if d > max_f_diff:
                max_f_diff = d
            terms.append((d / 0.001) ** 2)
    max_res = max(terms) if terms else 0.0
    
    # Trigger anomaly if mean standardized residual is elevated (> 0.0005),
    # or max sensor residual is significant (> 0.002), or physical deviation is evident (> 0.02m or > 0.00005 m³/s)
    is_anomalous = (
        baseline_score > 0.0005
        or max_res > 0.002
        or max_p_diff > 0.02
        or max_f_diff > 0.00005
    )

    return {'catalog': catalog, 'baseline': baseline,
            'baseline_score': baseline_score,
            'alert': {'anomalous': is_anomalous,
                      'standardised_residual': baseline_score,
                      'max_residual': max_res,
                      'max_pressure_diff_m': max_p_diff,
                      'max_flow_diff_m3s': max_f_diff,
                      'sensor_count': sensor_count,
                      'threshold': 0.0005},
            'started': time.monotonic()}


def context_agent(s: State):
    return {'context': fetch_context(s['network_name'])}

def rank_zones(s: State):
    zones = zones_for(network(s["network_name"]))

    if not s["alert"]["anomalous"]:
        return {
            "zones": zones,
            "zone_rankings": [],
            "selected_zone_ids": [],
            "expanded_zone_ids": [],
            "reserve": [],
        }

    # This reuses the existing offline signatures. No new WNTR runs here.
    ranked = shortlist(
        s["catalog"],
        s["observed"],
        limit=len(s["catalog"]["signatures"]),
    )

    zone_scores = {zone["id"]: [] for zone in zones}
    for item in ranked:
        zone_id = zone_for_candidate(zones, item["candidate"])
        zone_scores[zone_id].append(item["fingerprint_score"])

    zone_rankings = []
    for zone in zones:
        scores = sorted(zone_scores[zone["id"]])
        zone_rankings.append({
            "zone_id": zone["id"],
            "coarse_score": scores[0] if scores else float("inf"),
            "candidate_count": len(scores),
        })

    zone_rankings.sort(
        key=lambda item: (item["coarse_score"], item["zone_id"])
    )

    return {"zones": zones, "zone_rankings": zone_rankings}

def hypotheses(s: State):
    if not s["alert"]["anomalous"]:
        return {"pending": [], "reserve": [], "selected_zone_ids": []}

    ranked = shortlist(
        s["catalog"],
        s["observed"],
        limit=len(s["catalog"]["signatures"]),
    )

    selected_ids = [
        row["zone_id"] for row in s["zone_rankings"][:2]
    ]

    primary, outside = [], []
    for item in ranked:
        candidate = dict(item["candidate"])
        candidate["severity"] = estimate_severity(
            s["catalog"], s["observed"], item
        )
        zone_id = zone_for_candidate(s["zones"], candidate)
        entry = {
            "candidate": candidate,
            "fingerprint_score": item["fingerprint_score"],
            "zone_id": zone_id,
        }
        (primary if zone_id in selected_ids else outside).append(entry)

    # Reserve two of the online tests for outside the initial zones.
    primary_limit = max(2, s["budget"] - 2)
    pending = primary[:primary_limit]
    reserve = outside[:max(0, s["budget"] - len(pending))]

    return {
        "selected_zone_ids": selected_ids,
        "pending": pending,
        "reserve": reserve,
        "expanded_zone_ids": [],
    }

def worker(name, observed, item):
    h = Candidate(**item['candidate'])
    try:
        predicted = simulate(name, h)
        return {'candidate': asdict(h), 'score': score(observed, predicted),
                'fingerprint_score': item['fingerprint_score'],
                'zone_id': item['zone_id'],
                'predicted': predicted, 'status': 'ok'}
    except Exception as e:
        return {'candidate': asdict(h), 'zone_id': item.get('zone_id'), 'status': 'failed', 'error': str(e)}


def dispatcher(s: State):
    remaining = s['budget'] - len(s['rows'])
    batch = s['pending'][:min(2, remaining)]
    pending = s['pending'][len(batch):]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, s['network_name'], s['observed'], item)
                   for item in batch]
        new_rows = [future.result() for future in futures]
    rows = s['rows'] + new_rows
    return {'rows': rows, 'pending': pending,
            'events': s['events'] + [{'type': 'simulation', 'count': len(rows),
                         'tested': [{k: r.get(k) for k in
                                     ('candidate', 'zone_id', 'score', 'status')}
                                    for r in new_rows]}]}


def assess(s: State):
    sensor_count = (
        len(s['observed'].get('pressures', {}))
        + len(s['observed'].get('flows', {}))
    )
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
                 or gap < max(.02, fit * .5) or sensor_count < 2)
    return {'ranked': ranked, 'ambiguous': ambiguous,
            'reason': ('Scenarios overlap or the model fits poorly; obtain independent readings'
                       if ambiguous else 'Best tested type and location have a distinct fit')}


def evidence(s: State):
    return {'diagnosis': assess(s)}


def expand_zones(s: State):
    expanded = list(dict.fromkeys(
        row["zone_id"] for row in s["reserve"]
    ))
    return {
        "pending": s["reserve"],
        "reserve": [],
        "expanded_zone_ids": expanded,
    }


def route(s: State):
    if len(s['rows']) >= s['budget']:
        return "diagnose"
    ranked = s['diagnosis']['ranked']
    if s['pending']:
        # Only stop if the next precomputed competitor also lies well behind.
        if (
            len(s['rows']) >= 4
            and not s['diagnosis']['ambiguous']
            and ranked
            and s['pending'][0]['fingerprint_score']
                > ranked[0]['score'] + 0.25
        ):
            return "diagnose"
        return "simulate"
    # Initial zones were exhausted without a distinct adequate fit.
    if s['reserve'] and s['diagnosis']['ambiguous']:
        return "expand"
    return "diagnose"


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
    impact = None
    if d.get('ranked'):
        try:
            from .impact import analyze_impact
            impact = analyze_impact(s['network_name'], d['ranked'][0]['candidate'])
        except Exception:
            pass

    return {'report': {'network_name':s['network_name'],'alert':s['alert'],
            'ranked_faults':d['ranked'],'ambiguous':d['ambiguous'],
            'reason':d['reason'],'simulation_count':len(s['rows']),
            'offline_simulation_count':s['catalog']['offline_simulation_count'],
            'baseline_simulations':0,'isolation_simulations':int('isolation_what_if' in s['recommendation']),
            'candidate_space_size':len(s['catalog']['signatures']),
            'recommendation':s['recommendation'],
            'sensor_recommendation':s['sensor_recommendation'],'context':s['context'],
            'elapsed_ms':round((time.monotonic()-s['started'])*1000),
            'quantitative_impact': impact,
            'zone_search': {
                'ranked_zones': s.get('zone_rankings', []),
                'initial_zones': s.get('selected_zone_ids', []),
                'expanded_zones': s.get('expanded_zone_ids', []),
                'tested_zones': list(dict.fromkeys(
                    row['zone_id'] for row in s['rows'] if row.get('zone_id')
                )),
            }}}


def build_graph():
    g=StateGraph(State)
    for name,fn in [('monitor',monitor),('context',context_agent),
                    ('rank_zones',rank_zones),('hypotheses',hypotheses),
                    ('simulate',dispatcher),('expand',expand_zones),
                    ('evidence',evidence),('diagnose',diagnosis_agent),
                    ('sensor_planner',sensor_planner),('report',report_agent)]:
        g.add_node(name,instrument(name,fn))
    g.add_edge(START,'monitor')
    g.add_edge('monitor','context')
    g.add_edge('context','rank_zones')
    g.add_edge('rank_zones','hypotheses')
    g.add_node('no_alert',lambda s:{'diagnosis':{'ranked':[],'ambiguous':True,
                'reason':'No anomaly relative to model baseline'},
                'recommendation':{'action':'MONITOR','text':'No anomaly detected'}})
    g.add_conditional_edges('hypotheses',lambda s:'simulate' if s['pending'] else 'no_alert',
                             {'simulate':'simulate','no_alert':'no_alert'})
    g.add_edge('no_alert','sensor_planner')
    g.add_edge('simulate','evidence')
    g.add_conditional_edges('evidence', route,
                            {'simulate': 'simulate', 'expand': 'expand', 'diagnose': 'diagnose'})
    g.add_edge('expand', 'simulate')
    g.add_edge('diagnose','sensor_planner')
    g.add_edge('sensor_planner','report')
    g.add_edge('report',END)
    return g.compile()

GRAPH=build_graph()

def investigate(name,observed,budget=8):
    return GRAPH.invoke({'network_name':name,'observed':observed,'budget':budget,
                         'pending':[],'rows':[],'events':[]},
                        config={'recursion_limit':50})
