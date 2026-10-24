"""Offline hydraulic signatures and online shortlist retrieval."""
from __future__ import annotations
from dataclasses import asdict
from pathlib import Path
import json
import math
import wntr
from .models import Candidate
from .hydraulics import all_candidates, simulate
from .scoring import score

DATA = Path(__file__).resolve().parents[1] / 'data'
SCHEMA = 3


def path_for(name):
    clean = "".join(c for c in str(name) if c.isalnum() or c in ('_', '-'))
    if not clean:
        raise ValueError('Invalid network name')
    return DATA / f'{clean}_fingerprints.json'


def build_catalog(name='Net1'):
    """One-time simulation set, NOT part of the online investigation budget."""
    baseline = simulate(name)
    rows = []
    for h in all_candidates(name):
        try:
            rows.append({'candidate': asdict(h), 'predicted': simulate(name, h)})
        except Exception as exc:
            rows.append({'candidate': asdict(h), 'error': str(exc)})
    catalog = {'schema': SCHEMA, 'network_name': name, 'wntr_version': wntr.__version__,
               'offline_simulation_count': 1 + len(rows), 'baseline': baseline,
               'signatures': rows}
    DATA.mkdir(parents=True, exist_ok=True)
    path_for(name).write_text(json.dumps(catalog, separators=(',', ':')))
    return catalog


def load_catalog(name='Net1'):
    path = path_for(name)
    if not path.exists():
        raise FileNotFoundError(f'Build offline signatures first: python -m backend.fingerprints {name}')
    data = json.loads(path.read_text())
    if (data['schema'] != SCHEMA or data['network_name'] != name or
            data['wntr_version'] != wntr.__version__):
        raise ValueError('Fingerprint catalog is stale; rebuild it')
    return data


def _fingerprint_error(observed, predicted):
    # The same sensor scaling is used online and offline.
    return score(observed, predicted)


def shortlist(catalog, observed, limit=8):
    """One best severity per type+location; static lookups do not run WNTR."""
    by_location = {}
    for row in catalog['signatures']:
        if 'predicted' not in row:
            continue
        h = row['candidate']
        key = (h['fault_type'], h['location_id'])
        mismatch = _fingerprint_error(observed, row['predicted'])
        if key not in by_location or mismatch < by_location[key]['fingerprint_score']:
            by_location[key] = {'candidate': h, 'fingerprint_score': mismatch,
                                'predicted': row['predicted']}
    return sorted(by_location.values(), key=lambda r: (r['fingerprint_score'],
                  r['candidate']['fault_type'], r['candidate']['location_id']))[:limit]


def estimate_severity(catalog, observed, selected):
    """Interpolate neighbouring signature vectors and project observed residual onto them."""
    h = selected['candidate']
    family = sorted((r for r in catalog['signatures'] if 'predicted' in r and
                     r['candidate']['fault_type'] == h['fault_type'] and
                     r['candidate']['location_id'] == h['location_id']),
                    key=lambda r: r['candidate']['severity'])
    if len(family) < 2:
        return h['severity']
    best = (float('inf'), h['severity'])
    for left, right in zip(family, family[1:]):
        a, b = left['predicted'], right['predicted']
        dot = norm = 0.0
        for kind, sigma in (('pressures', .8), ('flows', .001)):
            for key, obs in observed.get(kind, {}).items():
                direction = (b[kind][key] - a[kind][key]) / sigma
                dot += (obs - a[kind][key]) / sigma * direction
                norm += direction * direction
        t = min(1., max(0., dot / norm)) if norm > 1e-16 else 0.
        interp = {kind: {key: a[kind][key] + t * (b[kind][key] - a[kind][key])
                         for key in a[kind]} for kind in ('pressures', 'flows')}
        error = score(observed, interp)
        severity = left['candidate']['severity'] + t * (
            right['candidate']['severity'] - left['candidate']['severity'])
        if error < best[0]:
            best = error, severity
    return round(best[1], 5)


if __name__ == '__main__':
    import sys
    name = sys.argv[1] if len(sys.argv) > 1 else 'Net1'
    data = build_catalog(name)
    print(f'{name}: {data["offline_simulation_count"]} offline runs; '
          f'{sum("predicted" in r for r in data["signatures"])} valid signatures')
