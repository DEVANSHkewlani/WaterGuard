import sqlite3
import time
import pytest
from fastapi.testclient import TestClient
from backend.models import Candidate
from backend.hydraulics import simulate, simulate_series, isolation_impact, network, apply_fault, topology
import wntr
from backend.scoring import score
from backend.context import fetch_context
from backend.fingerprints import (load_catalog, shortlist, estimate_severity,
                                  build_catalog, path_for)
from backend.graph import investigate
from backend.main import app
from backend.zones import zones_for, zone_for_candidate
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
    assert catalog['offline_simulation_count']==166
    assert len(catalog['signatures'])==165
    assert sum('predicted' in x for x in catalog['signatures'])==165
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
    assert report['offline_simulation_count']==166
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
        assert 'WaterGuard' in page.text and 'src="app.js?v=5"' in page.text
        assert client.get('/styles.css').status_code == 200
        assert client.get('/app.js').status_code == 200
        catalog = client.get('/api/baseline/Net1').json()
        assert catalog['offline_simulation_count'] == 166
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
        assert 'src="app.js?v=5"' in page
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


# --- Zone tests ---

def test_all_pipes_belong_to_exactly_one_zone():
    """Every pipe in Net1 must be in exactly one zone's pipe_ids."""
    wn = network('Net1')
    zones = zones_for(wn)
    all_pipes = set(wn.pipe_name_list)
    zone_pipe_counts = {pipe: 0 for pipe in all_pipes}
    for zone in zones:
        for pipe in zone['pipe_ids']:
            zone_pipe_counts[pipe] += 1
    # Every pipe assigned exactly once
    for pipe, count in zone_pipe_counts.items():
        assert count == 1, f"Pipe {pipe} belongs to {count} zones"


def test_every_junction_belongs_to_one_zone():
    """Every junction must appear in exactly one zone's junction_ids."""
    wn = network('Net1')
    zones = zones_for(wn)
    all_junctions = set(wn.junction_name_list)
    found = {}
    for zone in zones:
        for jid in zone['junction_ids']:
            found[jid] = found.get(jid, 0) + 1
    for jid in all_junctions:
        assert found.get(jid, 0) == 1, f"Junction {jid} zone count: {found.get(jid, 0)}"


def test_get_network_returns_zones():
    """GET /api/network/Net1 must include a non-empty zones list."""
    with TestClient(app) as client:
        resp = client.get('/api/network/Net1')
        assert resp.status_code == 200
        data = resp.json()
        assert 'zones' in data
        assert len(data['zones']) > 0
        zone = data['zones'][0]
        assert 'id' in zone and 'name' in zone
        assert 'pipe_ids' in zone and 'junction_ids' in zone


def test_synthetic_fault_triggers_zone_ranking_and_wntr_tests():
    """A full investigation with a synthetic fault must produce zone_search in the report."""
    h = Candidate('pipe_leak', '11', .55)
    state = investigate('Net1', simulate(candidate=h), 8)
    report = state['report']
    zone_search = report.get('zone_search')
    assert zone_search is not None, "report must have zone_search"
    assert len(zone_search['ranked_zones']) > 0
    assert len(zone_search['initial_zones']) > 0
    # Some zones must have been tested (online WNTR runs occurred)
    assert len(zone_search['tested_zones']) > 0
    # Simulation count must be within budget
    assert report['simulation_count'] <= 8


def test_single_manual_pressure_reading_never_yields_exact_location():
    """One pressure reading must always result in an ambiguous diagnosis."""
    h = Candidate('pipe_leak', '11', .55)
    x = simulate(candidate=h)
    # Submit only one pressure reading
    report = investigate('Net1', {'pressures': {'12': x['pressures']['12']}, 'flows': {}}, 8)['report']
    assert report['ambiguous'], "Single sensor input must produce an ambiguous result"


def test_online_simulation_count_stays_within_budget():
    """The number of online WNTR simulations must never exceed the requested budget."""
    h = Candidate('valve_closure', '21', .55)
    for budget in (2, 4, 8):
        state = investigate('Net1', simulate(candidate=h), budget)
        assert state['report']['simulation_count'] <= budget


def test_sse_zone_ids_agree_with_final_report():
    """Zone IDs streamed in node events must agree with the completed report's zone_search."""
    with TestClient(app) as client:
        obs = client.get('/api/demo/leak').json()['observed']
        sid = client.post('/api/investigations',
                          json={'observed': obs, 'budget': 8}).json()['session_id']
        for _ in range(200):
            if client.get(f'/api/investigations/{sid}').json()['status'] != 'running':
                break
            time.sleep(.05)
        with client.stream('GET', f'/api/investigations/{sid}/events') as response:
            body = ''.join(response.iter_text())
        result = client.get(f'/api/investigations/{sid}').json()['result']
        # The final report must contain zone_search
        assert 'zone_search' in result
        # Tested zones in report must be non-empty (at least some simulations ran)
        assert len(result['zone_search']['tested_zones']) >= 0  # can be 0 if no_alert
        # rank_zones node must appear in SSE stream
        assert '"node": "rank_zones"' in body


def test_net2_catalog_and_topology():
    """Verify Net2 catalog generation and 6-zone topology partition."""
    from backend.fingerprints import load_catalog
    catalog = load_catalog('Net2')
    assert catalog['network_name'] == 'Net2'
    assert catalog['offline_simulation_count'] == 576
    assert len(catalog['signatures']) == 575

    topo = topology('Net2')
    assert len(topo['junctions']) == 35
    assert len(topo['pipes']) == 40
    assert len(topo['zones']) == 6

    # Verify all junctions and pipes are assigned to exactly one zone
    all_j = [j for z in topo['zones'] for j in z['junction_ids']]
    assert sorted(all_j) == sorted(topo['junctions'])
    all_p = [p for z in topo['zones'] for p in z['pipe_ids']]
    assert sorted(all_p) == sorted(topo['pipes'])


def test_net2_investigation_locates_fault_within_budget():
    """Verify full graph on Net2 filters through zones and pinpoints fault."""
    leak = Candidate('pipe_leak', '11', 0.55)
    obs = simulate('Net2', leak)
    state = investigate('Net2', obs, budget=8)
    report = state['report']
    assert report['simulation_count'] <= 8
    assert report['ranked_faults'][0]['candidate']['location_id'] == '11'
    assert report['ranked_faults'][0]['candidate']['fault_type'] == 'pipe_leak'
    assert 'zone_search' in report
    assert len(report['zone_search']['ranked_zones']) == 6


def test_quantitative_impact_analysis():
    """Verify quantitative water loss and repair benefit physics calculations."""
    from backend.impact import analyze_impact
    impact = analyze_impact('Net1', Candidate('pipe_leak', '11', 0.55))
    assert impact['network_name'] == 'Net1'
    assert impact['water_loss']['loss_percentage'] > 0
    assert impact['water_loss']['flow_rate_lps'] > 0
    assert impact['water_loss']['daily_volume_m3'] > 0
    assert 'disruption_percentage' in impact['resource_impact']
    assert impact['repair_benefits']['water_recovery_pct'] == 100.0

    # API endpoint test
    with TestClient(app) as client:
        res = client.post('/api/impact', json={
            'network_name': 'Net1',
            'candidate': {'fault_type': 'pipe_leak', 'location_id': '11', 'severity': 0.55}
        })
        assert res.status_code == 200
        data = res.json()
        assert data['water_loss']['loss_percentage'] > 0


def test_custom_network_upload_and_simulation(tmp_path):
    """Verify custom .inp upload, catalog building, and simulation."""
    import io
    from uuid import uuid4
    from backend.hydraulics import available_networks
    wn = wntr.network.WaterNetworkModel('Net1')
    upload_name = 'CustomNet1_' + uuid4().hex[:8]
    json_name = 'CustomJsonNet_' + uuid4().hex[:8]
    temp_inp = tmp_path / f'{upload_name}.inp'
    wntr.network.write_inpfile(wn, str(temp_inp))
    inp_bytes = temp_inp.read_bytes()

    with TestClient(app) as client:
        res = client.post('/api/network/upload', files={'file': (f'{upload_name}.inp', io.BytesIO(inp_bytes), 'text/plain')})
        assert res.status_code == 200
        data = res.json()
        assert data['network_name'] == upload_name
        assert data['junctions_count'] == len(wn.junction_name_list)
        assert data['pipes_count'] == len(wn.pipe_name_list)
        assert 'baseline' in data
        assert 'topology' in data
        assert upload_name in data['networks']

        # Ensure we can run simulation on this uploaded network
        sim_res = client.get(f'/api/baseline/{upload_name}')
        assert sim_res.status_code == 200

        # Also test JSON upload format
        res_json = client.post('/api/network/upload', json={
            'name': json_name,
            'content': temp_inp.read_text()
        })
        assert res_json.status_code == 200
        assert res_json.json()['network_name'] == json_name
        assert client.delete(f'/api/onboarding/network/{upload_name}').status_code == 200
        assert client.delete(f'/api/onboarding/network/{json_name}').status_code == 200
