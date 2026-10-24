import sqlite3
import time
import pytest
from fastapi.testclient import TestClient
from backend.models import Candidate
from backend.hydraulics import simulate, simulate_series, isolation_impact, network, apply_fault
import wntr
from backend.scoring import score
from backend.context import fetch_context
from backend.fingerprints import (load_catalog, shortlist, estimate_severity,
                                  build_catalog, path_for)
from backend.graph import investigate
from backend.main import app
from evaluate import CASES, add_noise


def test_physics_isolated_and_faults_distinct():
    base=simulate()
    for h in (Candidate('pipe_leak','11',.55),
              Candidate('valve_closure','21',.55),
              Candidate('demand_surge','12',.55)):
        x=simulate(candidate=h)
        assert score(x,base)>0
        assert score(x,x)==pytest.approx(0)
    assert score(simulate(),base)<1e-18
    with pytest.raises(ValueError):
        simulate(candidate=Candidate('pipe_leak','missing',.55))


def test_catalog_has_offline_cost_and_valid_signatures():
    catalog=load_catalog('Net1')
    assert catalog['offline_simulation_count']==100
    assert len(catalog['signatures'])==99
    assert sum('predicted' in x for x in catalog['signatures'])==99
    observed=simulate(candidate=Candidate('valve_closure','21',.55))
    ranked=shortlist(catalog,observed,8)
    assert any(x['candidate']['fault_type']=='valve_closure' and
               x['candidate']['location_id']=='21' for x in ranked[:2])
    assert not any(x['candidate']['severity']==.55 for x in catalog['signatures'])
    estimate=estimate_severity(catalog,observed,ranked[0])
    assert .35<=estimate<=1


def test_stale_catalog_rejected(tmp_path,monkeypatch):
    from backend import fingerprints
    monkeypatch.setattr(fingerprints,'DATA',tmp_path)
    with pytest.raises(FileNotFoundError):
        load_catalog('Net1')


def test_sqlite_context_network_scope(tmp_path):
    db=tmp_path/'context.db'
    fetch_context('Net1',db)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO maintenance VALUES ('Net1','21',1,'planned')")
        conn.execute("INSERT INTO maintenance VALUES ('Net2','11',1,'different')")
    assert [x['location_id'] for x in fetch_context('Net1',db)['maintenance']]==['21']


@pytest.mark.parametrize('fault_type,location', [('pipe_leak','11'),('valve_closure','21'),
                                                 ('demand_surge','21')])
def test_full_graph_exact_on_heldout_severity(fault_type,location):
    h=Candidate(fault_type,location,.55)
    report=investigate('Net1',simulate(candidate=h),8)['report']
    assert report['alert']['anomalous']
    assert report['simulation_count']<=8
    assert report['offline_simulation_count']==100
    assert report['ranked_faults'][0]['candidate']['fault_type']==fault_type
    assert report['ranked_faults'][0]['candidate']['location_id']==location
    assert report['ranked_faults'][0]['explanation']


def test_sparse_sensor_does_not_claim_detection():
    h=Candidate('pipe_leak','11',.55)
    x=simulate(candidate=h)
    report=investigate('Net1',{'pressures':{'12':x['pressures']['12']},'flows':{}},8)['report']
    assert report['ambiguous']
    assert report['simulation_count']==0


def test_ten_noisy_cases_top_two_regression():
    hits=0
    for i,h in enumerate(CASES):
        report=investigate('Net1',add_noise(simulate(candidate=h),100+i),8)['report']
        hits+=any(r['candidate']['fault_type']==h.fault_type and
                  r['candidate']['location_id']==h.location_id
                  for r in report['ranked_faults'][:2])
        assert report['simulation_count']<=8
    assert hits>=9


def test_isolation_what_if_assumption():
    result=isolation_impact('Net1','11')
    assert 'simulated_safe' in result and 'assumption' in result


def test_api_validation_demo_and_session():
    with TestClient(app) as client:
        assert client.get('/api/network/Net1').status_code==200
        assert client.get('/api/network/bad').status_code==404
        assert client.post('/api/investigate',json={'observed':{'pressures':{'bad':10}}}).status_code==422
        demo=client.get('/api/demo/leak').json()
        assert demo['ground_truth']['severity']==.55
        request={'network_name':'Net1','observed':demo['observed'],'budget':8}
        response=client.post('/api/investigate',json=request)
        assert response.status_code==200,response.text
        assert response.json()['ranked_faults'][0]['candidate']['location_id']=='11'
        created=client.post('/api/investigations',json=request)
        assert created.status_code==202
        sid=created.json()['session_id']
        for _ in range(100):
            session=client.get(f'/api/investigations/{sid}').json()
            if session['status']!='running':break
            time.sleep(.05)
        assert session['status']=='complete'
        assert session['result']['simulation_count']<=8


def test_sse_replays_finished_run():
    with TestClient(app) as client:
        obs=client.get('/api/demo/leak').json()['observed']
        sid=client.post('/api/investigations',json={'observed':obs,'budget':4}).json()['session_id']
        for _ in range(100):
            if client.get(f'/api/investigations/{sid}').json()['status']!='running':break
            time.sleep(.05)
        with client.stream('GET',f'/api/investigations/{sid}/events') as response:
            body=''.join(response.iter_text())
        assert '"node": "simulate"' in body
        assert '"type": "complete"' in body
        assert '"elapsed_ms"' in body
        assert '"leaders"' in body


def test_plain_site_and_projection_match_alert_snapshot():
    with TestClient(app) as client:
        page = client.get('/')
        assert page.status_code == 200
        assert 'WaterGuard' in page.text and 'src="app.js?v=3"' in page.text
        assert client.get('/styles.css').status_code == 200
        assert client.get('/app.js').status_code == 200
        catalog = client.get('/api/baseline/Net1').json()
        assert catalog['offline_simulation_count'] == 100
        demo = client.get('/api/demo/leak').json()['observed']
        projection = client.get('/api/demo/leak/series').json()
        assert projection['baseline']['hours'] == list(range(13))
        for key in ('12', '22'):
            assert projection['fault']['pressures'][key][0] == pytest.approx(demo['pressures'][key])
        for key in ('11', '21'):
            assert projection['fault']['flows'][key][0] == pytest.approx(demo['flows'][key])
        manual = client.post('/api/compare-series', json={
            'network_name': 'Net1',
            'candidate': {'fault_type': 'pipe_leak', 'location_id': '11', 'severity': .55}})
        assert manual.status_code == 200
        assert manual.json()['fault']['flows']['11'][0] == pytest.approx(demo['flows']['11'])
        invalid = client.post('/api/compare-series', json={
            'candidate': {'fault_type': 'pipe_leak', 'location_id': 'missing', 'severity': .55}})
        assert invalid.status_code == 422


def test_custom_fault_injection_is_ready_for_investigation():
    with TestClient(app) as client:
        page = client.get('/').text
        assert 'src="app.js?v=3"' in page
        assert 'id="injectFault"' in page and 'id="addReading"' in page
        custom = client.post('/api/demo/custom', json={
            'fault_type': 'pipe_leak', 'location_id': '11', 'severity': .55})
        assert custom.status_code == 200, custom.text
        payload = custom.json()
        assert payload['ground_truth']['location_id'] == '11'
        assert payload['series']['fault']['pressures']['12'][0] == pytest.approx(
            payload['observed']['pressures']['12'])
        report = client.post('/api/investigate', json={
            'network_name': 'Net1', 'observed': payload['observed'], 'budget': 8})
        assert report.status_code == 200, report.text
        assert report.json()['ranked_faults'][0]['candidate']['location_id'] == '11'
        assert client.post('/api/demo/custom', json={
            'fault_type': 'demand_surge', 'location_id': '11', 'severity': 1.5}).status_code == 422
        assert client.post('/api/demo/custom', json={
            'fault_type': 'demand_surge', 'location_id': '110', 'severity': .55}).status_code == 422


def test_observability_status_is_truthful_without_keys(monkeypatch):
    monkeypatch.delenv('LANGSMITH_API_KEY', raising=False)
    monkeypatch.delenv('AGENTOPS_API_KEY', raising=False)
    from backend.observability import status
    with TestClient(app) as client:
        result=client.get('/api/observability')
        assert result.status_code==200
        data=result.json()
        assert data['local_timeline']
        assert not data['langsmith_configured']
        assert not data['agentops_configured']


def test_topology_and_demo_include_every_original_sensor():
    with TestClient(app) as client:
        topology=client.get('/api/network/Net1').json()
        demo=client.get('/api/demo/leak').json()['observed']
        assert len(topology['junctions']) == 9
        assert len(topology['pipes']) == 12
        assert set(topology['junctions']) == set(demo['pressures'])
        assert set(topology['pipes']) == set(demo['flows'])


def test_online_verification_is_a_fresh_hydraulic_run():
    model=network('Net1')
    assert isinstance(model, wntr.network.WaterNetworkModel)
    apply_fault(model, Candidate('pipe_leak','11',.55))
    assert '_leak_node' in model.junction_name_list
    h=Candidate('pipe_leak','11',.55)
    actual=simulate(candidate=h)
    signatures=[r for r in load_catalog()['signatures'] if
                r['candidate']['fault_type']=='pipe_leak' and r['candidate']['location_id']=='11']
    low,high=signatures[:2]
    ratio=(.55-.35)/(.70-.35)
    interpolated=low['predicted']['pressures']['12']+ratio*(
        high['predicted']['pressures']['12']-low['predicted']['pressures']['12'])
    assert abs(actual['pressures']['12']-interpolated)>1e-5


def test_context_cli_accepts_valid_records(tmp_path):
    from manage_context import main
    db=tmp_path/'context.sqlite3'
    main(['add-maintenance','--pipe','21','--planned','--note','Demo planned valve work'], db)
    main(['add-demand','--junction','12','--multiplier','1.2','--note','Demo schedule'], db)
    rows=fetch_context('Net1',db)
    assert rows['maintenance'][0]['planned'] == 1
    assert rows['maintenance'][0]['location_id'] == '21'
    assert rows['demand_schedule'][0]['multiplier'] == pytest.approx(1.2)

def test_low_strength_j21_surge_is_found():
    truth = Candidate('demand_surge', '21', 0.19)
    observed = simulate(candidate=truth)
    report = investigate('Net1', observed, 8)['report']

    best = report['ranked_faults'][0]
    assert best['candidate']['fault_type'] == 'demand_surge'
    assert best['candidate']['location_id'] == '21'
    assert best['candidate']['severity'] == pytest.approx(0.19, abs=0.02)
    assert best['score'] < 0.02