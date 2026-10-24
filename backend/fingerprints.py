"""Offline hydraulic signatures and online shortlist retrieval."""
from __future__ import annotations
from dataclasses import asdict
from pathlib import Path
import json
import math
import hashlib
import os
import wntr
from .models import Candidate, SEVERITIES
from .hydraulics import (all_candidates, simulate, network, UPLOADS_DIR,
                         LEAK_AREA_M2, CLOSURE_LOSS_FACTOR, SURGE_DEMAND_M3S)
from .scoring import score

DATA = Path(__file__).resolve().parents[1] / 'data'
SCHEMA = 3
LEGACY_BUILTIN_CONFIG = (0.00035, 300, 0.012, (0.05, 0.20, 0.35, 0.70, 1.0))

def model_fingerprint(name):
    """Bind catalogs to the actual model bytes and fault grid, not its display name."""
    source = UPLOADS_DIR / f'{name}.inp'
    if not source.exists():
        source = DATA / f'{name}.inp'
    if source.exists():
        model = source.read_bytes()
    elif name in ('Net1', 'Net2'):
        model = f'wntr-bundled:{name}'.encode()
    else:
        raise ValueError('Network model is missing')
    return hashlib.sha256(model + repr((wntr.__version__, SEVERITIES,
        LEAK_AREA_M2, CLOSURE_LOSS_FACTOR, SURGE_DEMAND_M3S)).encode()).hexdigest()


def path_for(name):
    clean = "".join(c for c in str(name) if c.isalnum() or c in ('_', '-'))
    if not clean:
        raise ValueError('Invalid network name')
    return DATA / f'{clean}_fingerprints.json'


def build_catalog(name='Net1', progress=None, max_candidates=None):
    """One-time simulation set, NOT part of the online investigation budget."""
    candidates = all_candidates(name)
    if max_candidates is not None and len(candidates) > max_candidates:
        raise ValueError(f'{len(candidates)} fault scenarios exceed the {max_candidates} scenario budget; simplify the network')
    baseline = simulate(name)
    rows = []
    for h in candidates:
        try:
            rows.append({'candidate': asdict(h), 'predicted': simulate(name, h)})
        except Exception as exc:
            rows.append({'candidate': asdict(h), 'error': str(exc)})
        if progress:
            progress(len(rows), len(candidates))
    catalog = {'schema': SCHEMA, 'network_name': name, 'wntr_version': wntr.__version__,
               'model_fingerprint': model_fingerprint(name),
               'offline_simulation_count': 1 + len(rows), 'baseline': baseline,
               'signatures': rows}
    DATA.mkdir(parents=True, exist_ok=True)
    if not any('predicted' in row for row in rows):
        raise ValueError('No supported fault simulations succeeded for this network')
    target = path_for(name)
    temporary = target.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(catalog, separators=(',', ':')))
    os.replace(temporary, target)
    return catalog


def load_catalog(name='Net1'):
    path = path_for(name)
    if not path.exists():
        raise FileNotFoundError(f'Build offline signatures first: python -m backend.fingerprints {name}')
    data = json.loads(path.read_text())
    if (data['schema'] != SCHEMA or data['network_name'] != name or
            data['wntr_version'] != wntr.__version__):
        raise ValueError('Fingerprint catalog is stale; rebuild it')
    fingerprint = model_fingerprint(name)
    if data.get('model_fingerprint') != fingerprint:
        # Bundled demonstration catalogs predate this field and are immutable fixtures.
        if not (name in ('Net1', 'Net2') and 'model_fingerprint' not in data and
                (LEAK_AREA_M2, CLOSURE_LOSS_FACTOR, SURGE_DEMAND_M3S, SEVERITIES) == LEGACY_BUILTIN_CONFIG):
            raise ValueError('Fingerprint catalog is stale for this model; rebuild it')
    return data


def _fingerprint_error(observed, predicted):
    # The same sensor scaling is used online and offline.
    return score(observed, predicted)


def _interpolated_best(family, observed):
    """Find the best interpolated score across adjacent severity pairs.

    For each pair of consecutive severity catalog entries, project the observed
    readings onto the line segment between them to find the severity (and
    predicted readings) that minimises the score.  This makes the shortlist
    ranking independent of how coarse the severity grid is.
    """
    best_score = float('inf')
    best_predicted = None
    best_severity = None

    # Also consider each raw catalog entry on its own (t=0 of each interval).
    for entry in family:
        raw = _fingerprint_error(observed, entry['predicted'])
        if raw < best_score:
            best_score, best_predicted, best_severity = (
                raw, entry['predicted'], entry['candidate']['severity'])

    for left, right in zip(family, family[1:]):
        a, b = left['predicted'], right['predicted']
        dot = norm = 0.0
        for kind, sigma in (('pressures', .8), ('flows', .001)):
            for key, obs in observed.get(kind, {}).items():
                va, vb = a[kind].get(key, 0.), b[kind].get(key, 0.)
                direction = (vb - va) / sigma
                dot += (obs - va) / sigma * direction
                norm += direction * direction
        t = min(1., max(0., dot / norm)) if norm > 1e-16 else 0.
        interp = {kind: {key: a[kind][key] + t * (b[kind][key] - a[kind][key])
                         for key in a[kind]} for kind in ('pressures', 'flows')}
        error = score(observed, interp)
        if error < best_score:
            sev = left['candidate']['severity'] + t * (
                right['candidate']['severity'] - left['candidate']['severity'])
            best_score, best_predicted, best_severity = error, interp, sev

    return best_score, best_predicted, best_severity


def shortlist(catalog, observed, limit=8):
    """One best severity per type+location, using interpolated scoring.

    For every (fault_type, location_id) family in the catalog, we interpolate
    between adjacent severity entries to find the predicted readings that best
    explain the observations.  This makes the ranking accurate even when the
    true severity is not on the pre-computed grid.
    """
    # Group catalog rows by (fault_type, location_id)
    families: dict[tuple, list] = {}
    for row in catalog['signatures']:
        if 'predicted' not in row:
            continue
        h = row['candidate']
        key = (h['fault_type'], h['location_id'])
        families.setdefault(key, []).append(row)

    by_location = {}
    for key, rows in families.items():
        rows.sort(key=lambda r: r['candidate']['severity'])
        best_score, best_predicted, best_severity = _interpolated_best(rows, observed)
        if best_predicted is not None:
            best_candidate = dict(rows[0]['candidate'], severity=best_severity)
            by_location[key] = {'candidate': best_candidate,
                                'fingerprint_score': best_score,
                                'predicted': best_predicted}

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
