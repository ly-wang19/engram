"""Model ownership, secret handling, durable source events and atomic product state."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
import stat

import pytest

from engram.management.store import ManagementStore
from engram.management.models import ModelManager
from engram.service import MemoryService


@pytest.fixture
def service(tmp_path, monkeypatch):
    from engram.llm import providers
    monkeypatch.setattr(providers, 'load_dotenv', lambda: None)
    for name in ('ENGRAM_LLM', 'ENGRAM_ANSWERER', 'ENGRAM_VISION_LLM', 'ENGRAM_ASYNC_CONSOLIDATION'):
        monkeypatch.delenv(name, raising=False)
    svc = MemoryService(str(tmp_path), embedder_name='hashing', llm_name='')
    yield svc
    svc.close()


class ProbeClient:
    def __init__(self, row):
        self.model = row['model']
        self.secret = row.get('api_key')
        self.max_attempts = 4

    def complete(self, prompt, **kwargs):
        assert prompt == 'Reply with the single word OK.'
        assert kwargs['timeout'] <= 15 and kwargs['num_retries'] == 0
        assert self.max_attempts == 1
        if self.model.endswith('failure'):
            raise ConnectionError('provider echoed ' + (self.secret or 'secret'))
        return 'OK'


def test_store_atomic_update_reopen_and_tenant_isolation(tmp_path):
    store = ManagementStore(str(tmp_path))
    def increment(_):
        store.update('tenant', 'counter', 'one', lambda old: {'count': (old or {}).get('count', 0) + 1})
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(increment, range(60)))
    store.put('other', 'counter', 'one', {'count': 900})
    reopened = ManagementStore(str(tmp_path))
    assert reopened.get('tenant', 'counter', 'one') == {'count': 60}
    if os.name != "nt":  # Windows keeps only a read-only bit
        assert stat.S_IMODE(os.stat(store.path).st_mode) == 0o600
    reopened.clear_namespace('tenant')
    assert reopened.get('other', 'counter', 'one') == {'count': 900}
    assert reopened.scan('counter') == [('other', 'one', {'count': 900})]


def test_model_runtime_reopen_secret_mask_and_endpoint_change(service, monkeypatch):
    monkeypatch.setattr(ModelManager, '_client', staticmethod(ProbeClient))
    old_memory = service.get('synthetic')
    out = service.models.configure('extractor', {'mode': 'custom', 'model': 'openai/synthetic', 'api_base': 'http://localhost:9999/v1', 'api_key': 'synthetic-secret'})
    assert 'synthetic-secret' not in json.dumps(out)
    assert out['roles'][0]['key_present'] is True
    assert service.llm is old_memory.llm and service.answerer is service.llm
    assert old_memory.summarizer.llm is service.llm
    assert service.models.test('extractor')['ok'] is True
    assert service.llm.max_attempts == 4
    fresh = MemoryService(service.data_dir, embedder_name='hashing', llm_name='')
    assert fresh.llm.model == 'openai/synthetic' and fresh.llm.secret == 'synthetic-secret'
    fresh.models.configure('extractor', {'mode': 'custom', 'model': 'openai/synthetic', 'api_base': 'http://localhost:9998/v1'})
    assert fresh.llm.secret == ''
    assert fresh.models.view()['roles'][0]['last_test'] is None
    fresh.close()


def test_probe_failure_and_disabled_model_are_not_success(service, monkeypatch):
    monkeypatch.setattr(ModelManager, '_client', staticmethod(ProbeClient))
    service.models.configure('answerer', {'mode': 'custom', 'model': 'openai/failure', 'api_key': 'never-echo-this'})
    result = service.models.test('answerer')
    assert result['ok'] is False and result['error'] == 'ConnectionError'
    assert 'never-echo-this' not in json.dumps(service.models.view())
    service.models.configure('answerer', {'mode': 'offline'})
    assert service.models.test('answerer')['error'] == 'model_not_configured'


def test_invalid_model_configuration_preserves_previous(service, monkeypatch):
    monkeypatch.setattr(ModelManager, '_client', staticmethod(ProbeClient))
    service.models.configure('extractor', {'mode': 'custom', 'model': 'openai/synthetic'})
    before = service.models.view()
    for payload in ({'api_base': 'https://name:secret@example.com/v1'}, {'timeout': 0}, {'timeout': float('nan')}):
        with pytest.raises(ValueError):
            service.models.configure('extractor', {'mode': 'custom', 'model': 'openai/new', **payload})
        assert service.models.view() == before


def test_connection_events_actual_success_failure_and_restart(service):
    service.remember('alice', 'I live in Example City.', session_id='codex:test')
    with pytest.raises(ValueError):
        service.import_('alice', format='does-not-exist', data='bad')
    events = service.connections.events('alice')
    assert {e['status'] for e in events['items']} == {'stored', 'failed'}
    assert 'Example City' not in json.dumps(events)
    assert service.connections.events('bob')['total'] == 0
    fresh = MemoryService(service.data_dir, embedder_name='hashing', llm_name='')
    assert fresh.connections.events('alice')['total'] == 2
    rows = fresh.connections.index('alice')['sources']
    assert any(r['source'] == 'codex' and r['episodes'] == 1 for r in rows)
    fresh.close()


def test_forget_clears_tenant_records_not_global_models(service):
    service.management_store.put('', 'models', 'answerer', {'role': 'answerer', 'mode': 'offline'})
    service.connections.record('alice', 'codex', 'failed', 'sync', error='ConnectionError')
    service.connections.record('bob', 'codex', 'stored', 'sync')
    service.jobs.enqueue('alice', 'codex:test', start=False)
    service.forget('alice')
    assert service.connections.events('alice')['total'] == 0
    assert service.jobs.list('alice')['page']['total'] == 0
    assert service.connections.events('bob')['total'] == 1
    assert service.management_store.get('', 'models', 'answerer')['mode'] == 'offline'


def test_admin_authorization_and_no_secret_readback(service, monkeypatch):
    pytest.importorskip('fastapi')
    from fastapi.testclient import TestClient
    from engram.server import app as appmod
    monkeypatch.setenv('ENGRAM_OPEN', '1')
    monkeypatch.delenv('ENGRAM_API_KEYS', raising=False)
    monkeypatch.setenv('ENGRAM_ADMIN_KEYS', 'synthetic-operator-token')
    monkeypatch.setattr(appmod, '_svc', service)
    monkeypatch.setattr(ModelManager, '_client', staticmethod(ProbeClient))
    user = {'Authorization': 'Bearer synthetic-user'}
    admin = {**user, 'X-Engram-Admin-Key': 'synthetic-operator-token'}
    with TestClient(appmod.app) as client:
        assert client.get('/v1/admin/models', headers=user).status_code == 403
        assert client.put('/v1/admin/models/extractor', headers=user, json={'mode': 'offline'}).status_code == 403
        response = client.put('/v1/admin/models/extractor', headers=admin, json={'mode': 'custom', 'model': 'openai/synthetic', 'api_key': 'synthetic-model-secret'})
        assert response.status_code == 200
        assert 'synthetic-model-secret' not in response.text
        assert 'synthetic-model-secret' not in client.get('/v1/admin/models', headers=admin).text
        assert client.post('/v1/admin/models/extractor/test', headers=admin).json()['ok'] is True
        assert client.get('/v1/admin/access', headers=user).json() == {'configured': True, 'is_admin': False}
        event = client.post('/v1/connections/report', headers=user, json={'source': 'codex', 'status': 'failed', 'operation': 'sync', 'error': 'raw secret text'})
        assert event.status_code == 200 and event.json()['error'] is None
        assert client.get('/v1/connections/events', headers={'Authorization': 'Bearer another-user'}).json()['total'] == 0


def test_real_litellm_adapter_against_local_http_provider(service, monkeypatch):
    """Exercise actual request serialization, timeout/key forwarding and failure reporting, offline."""
    monkeypatch.setenv('LITELLM_LOCAL_MODEL_COST_MAP', 'True')
    pytest.importorskip('litellm')
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    received = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            received.append((self.path, self.headers.get('Authorization'), body))
            failed = body['model'] == 'synthetic-failure'
            self.send_response(401 if failed else 200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            response = {'error': {'message': 'synthetic provider refused synthetic-key', 'type': 'invalid_api_key'}} if failed else {
                'id': 'synthetic-response', 'object': 'chat.completion', 'created': 1,
                'model': body['model'], 'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'OK'}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}
            self.wfile.write(json.dumps(response).encode())
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        payload = {'mode': 'custom', 'model': 'openai/synthetic-success',
                   'api_base': f'http://127.0.0.1:{server.server_port}/v1', 'api_key': 'synthetic-key', 'timeout': 2}
        service.models.configure('answerer', payload)
        assert service.models.test('answerer')['ok'] is True
        assert received[0][0] == '/v1/chat/completions'
        assert received[0][1] == 'Bearer synthetic-key'
        assert received[0][2]['max_tokens'] == 8
        service.models.configure('answerer', {**payload, 'model': 'openai/synthetic-failure'})
        failure = service.models.test('answerer')
        assert failure['ok'] is False and failure['error'] == 'AuthenticationError'
        assert 'synthetic-key' not in json.dumps(failure)
        assert len(received) == 2
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
