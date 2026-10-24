"""Held-out severity and reproducible noisy sensor evaluation."""
import random
from backend.models import Candidate
from backend.hydraulics import simulate
from backend.graph import investigate

CASES = [Candidate('pipe_leak', p, .55) for p in ('11','12','21','22')]
CASES += [Candidate('valve_closure',p,.55) for p in ('11','21','22')]
CASES += [Candidate('demand_surge',n,.55) for n in ('12','21','22')]

def add_noise(observed, seed):
    rng=random.Random(seed)
    return {'pressures':{k:v+rng.gauss(0,.02) for k,v in observed['pressures'].items()},
            'flows':{k:v+rng.gauss(0,.00003) for k,v in observed['flows'].items()}}

def run(noisy=False):
    top1=top2=distinct=0
    counts=[]
    for i,hidden in enumerate(CASES):
        readings=simulate(candidate=hidden)
        if noisy:
            readings=add_noise(readings,100+i)
        report=investigate('Net1',readings,8)['report']
        ranked=report['ranked_faults']
        match=lambda r:r['candidate']['fault_type']==hidden.fault_type and r['candidate']['location_id']==hidden.location_id
        one=bool(ranked and match(ranked[0]))
        two=any(match(r) for r in ranked[:2])
        top1+=one;top2+=two;distinct+=not report['ambiguous'];counts.append(report['simulation_count'])
        print(f'{hidden.fault_type:14} {hidden.location_id:>3} '
              f'top1={one!s:5} top2={two!s:5} '
              f'online={report["simulation_count"]:2} ambiguous={report["ambiguous"]}')
    print(f'{"NOISY" if noisy else "CLEAN"}: top1={top1}/10, top2={top2}/10, '
          f'distinct={distinct}/10; online runs total={sum(counts)}, '
          f'average={sum(counts)/len(counts):.1f}')
    return top1,top2,distinct

if __name__=='__main__':
    run(False)
    run(True)
