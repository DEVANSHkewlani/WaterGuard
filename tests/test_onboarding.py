from io import BytesIO
import time
from PIL import Image
from fastapi.testclient import TestClient
from backend.main import app
from backend.onboarding import Draft, Place, Connection, guided_draft, inspect_document, validate_draft
from backend.fingerprints import load_catalog
from backend.hydraulics import UPLOADS_DIR
import pytest


def drawing(kind='PNG'):
    out = BytesIO()
    Image.new('RGB', (80, 60), 'white').save(out, format=kind)
    return out.getvalue()


def test_diagram_validation_and_manual_trace():
    with TestClient(app) as client:
        for filename, data in [('wrong.png', b'not an image'), ('wrong.pdf', b'%PDF-broken'),
                               ('huge.png', b'x' * (10 * 1024 * 1024 + 1))]:
            response = client.post('/api/onboarding/diagram', files={'file': (filename, data)})
            assert response.status_code == 422
        response = client.post('/api/onboarding/diagram', files={'file': ('house.png', drawing())})
        assert response.status_code == 200, response.text
        draft = response.json()['draft']
        assert draft['nodes'] == [] and draft['links'] == []
        assert draft['document']['type'] == 'png'
        assert client.get('/api/onboarding/document/' + draft['id']).content == drawing()
        # The uploaded drawing never becomes a fabricated hydraulic topology.
        assert not client.post('/api/onboarding/validate', json={'draft': draft, 'estimated': True}).json()['structural_valid']
        with TestClient(app) as other:
            assert other.get('/api/onboarding/document/' + draft['id']).status_code == 404
        assert client.delete('/api/onboarding/network/' + draft['id']).status_code == 200


def test_pdf_page_validation():
    result = inspect_document('house.pdf', drawing('PDF'))
    assert result['pages'] == 1 and result['type'] == 'pdf'
    assert 'text_excerpt' in result


def test_structural_corrections_and_unknown_values():
    draft = Draft(nodes=[Place(id='Source', name='Supply', kind='source'),
                         Place(id='Sink', name='Kitchen', kind='outlet')],
                  links=[Connection(id='Pipe1', name='Pipe', start='Source', end='Missing')])
    assert any('endpoints' in error for error in validate_draft(draft, True)['errors'])
    draft.links[0].end = 'Sink'
    check = validate_draft(draft, False)
    assert not check['structural_valid'] and any('unknown' in e for e in check['errors'])
    estimated = validate_draft(draft, True)
    assert estimated['structural_valid'] and estimated['assumptions']
    draft.links[0].end = 'Source'
    assert not validate_draft(draft, True)['structural_valid']


def test_guided_to_simulated_investigation_and_isolation():
    with TestClient(app) as client:
        source = client.post('/api/onboarding/guided', json={
            'source': 'municipal', 'pump': False, 'outlets': 'Kitchen, Bathroom',
            'issue': 'low pressure'}).json()['draft']
        assert len(source['nodes']) == 4
        assert source['nodes'][-1]['name'] == 'Bathroom'
        check = client.post('/api/onboarding/validate', json={'draft': source, 'estimated': True})
        assert check.status_code == 200 and check.json()['hydraulic_valid']
        published = client.post('/api/onboarding/commit', json={'draft': source, 'estimated': True})
        assert published.status_code == 202, published.text
        name = published.json()['network_name']
        assert name != source['id']
        try:
            for _ in range(100):
                status = client.get('/api/onboarding/catalog/' + name).json()
                if status['status'] != 'building':
                    break
                time.sleep(.1)
            assert status['status'] == 'ready', status
            catalog = load_catalog(name)
            assert catalog['model_fingerprint']
            model_path = UPLOADS_DIR / f'{name}.inp'
            original_model = model_path.read_bytes()
            model_path.write_bytes(original_model + b'\n; changed\n')
            with pytest.raises(ValueError, match='stale'):
                load_catalog(name)
            model_path.write_bytes(original_model)
            topology = client.get('/api/network/' + name).json()
            assert any(n['data']['name'] == 'Bathroom' for n in topology['nodes'])
            assert name in client.get('/api/networks').json()['networks']
            with TestClient(app) as other:
                assert other.get('/api/network/' + name).status_code == 404
                assert other.get('/api/baseline/' + name).status_code == 404
            test = client.post('/api/demo/custom', json={'network_name': name,
                'fault_type': 'pipe_leak', 'location_id': 'Pipe1', 'severity': .55})
            assert test.status_code == 200, test.text
            report = client.post('/api/investigate', json={'network_name': name,
                'observed': test.json()['observed'], 'observation_source': 'simulated'})
            assert report.status_code == 200, report.text
            body = report.json()
            assert body['model_fingerprint'] == catalog['model_fingerprint']
            assert body['model_validation']['estimated']
            assert body['diagnostic_status'] == 'exploratory_only'
            assert body['quantitative_impact'] is None
            assert body['simulation_count'] <= 8
            assert client.post('/api/impact', json={'network_name': name,
                'candidate': {'fault_type': 'pipe_leak', 'location_id': 'Pipe1', 'severity': .55}}).status_code == 422
            assert client.put('/api/onboarding/sensors/' + name, json={'sensors': [{
                'sensor_id': 'KitchenPressure', 'element_id': 'Outlet1',
                'kind': 'pressure', 'unit': 'kPa', 'interval_s': 5}]}).status_code == 200
            sample = client.get('/api/onboarding/sensors/' + name + '/sample').json()
            assert sample['source'] == 'simulated' and sample['readings'][0]['unit'] == 'kPa'
            assert sample['readings'][0]['value'] > 0
            assert client.put('/api/onboarding/sensors/' + name, json={'sensors': [{
                'sensor_id': 'Bad', 'element_id': 'Pipe1', 'kind': 'pressure',
                'unit': 'm', 'interval_s': 5}]}).status_code == 422
        finally:
            assert client.delete('/api/onboarding/network/' + name).status_code == 200
            assert not (UPLOADS_DIR / f'{name}.inp').exists()
            assert client.get('/api/onboarding/draft/' + source['id']).status_code == 404
