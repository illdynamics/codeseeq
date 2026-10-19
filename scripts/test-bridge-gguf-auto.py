#!/usr/bin/env python3
"""Regression: gguf@auto discovers the live model and never requires a path.

Exercises actual loopback HTTP model discovery, hot-swapping and /v1/responses
chat-completions translation, without llama.cpp or external credentials.
"""
import asyncio
import importlib.util
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

repo = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('codeseeq_bridge', repo / 'bin/codeseeq-bridge.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)

class Handler(BaseHTTPRequestHandler):
    model = 'loaded-model-A.gguf'
    models = None
    requests = []
    auth = None
    def log_message(self, *args):
        return
    def _json(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        if Handler.auth and self.headers.get('Authorization') != f'Bearer {Handler.auth}':
            return self._json(401, {'error': 'key missing'})
        if self.path in ('/v1/models', '/models'):
            return self._json(200, {'object':'list', 'data': Handler.models or [{'id':Handler.model}]})
        return self._json(404, {'error': 'not found'})
    def do_POST(self):
        if self.path != '/v1/chat/completions':
            return self._json(404, {'error':'not found'})
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
        Handler.requests.append(body)
        return self._json(200, {
            'id':'chatcmpl-test', 'object':'chat.completion', 'model':body.get('model'),
            'choices':[{'index':0,'message':{'role':'assistant','content':'hello'}, 'finish_reason':'stop'}],
            'usage':{'prompt_tokens':5, 'completion_tokens':2, 'total_tokens':7},
        })

server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
base = f'http://127.0.0.1:{server.server_port}'
old = {k: os.environ.get(k) for k in ('GGUF_BASE_URL','GGUF_API_KEY','CODESEEQ_MODEL','CODESEEQ_PROVIDER', 'LOCAL_BASE_URL', 'OPENAI_BASE_URL', 'CODESEEQ_BASE_URL')}
os.environ['GGUF_BASE_URL'] = base + '/v1'
os.environ['CODESEEQ_MODEL'] = 'gguf@auto'
os.environ['CODESEEQ_PROVIDER'] = 'gguf'

class FakeRequest:
    def __init__(self, body): self.body = body
    async def json(self): return self.body

def check(ok, msg):
    assert ok, msg
    print('[gguf-auto] PASS', msg)

async def run():
    model = bridge.normalize_model('gguf@auto')
    check(model.gguf_path is None, 'gguf@auto never resolves as a filesystem path')
    check((model.context_window, model.max_output_tokens) == (131072, 65536), 'GGUF default context and output limits')
    check((model.temperature, model.top_p, model.top_k) == (0.6, 0.95, 20), 'GGUF default sampling')
    check(bridge.resolve_provider_for_slug('gguf@auto') == bridge.PROVIDER_GGUF, 'routes as GGUF')
    endpoint, mid = await bridge.discover_gguf_auto()
    check((endpoint, mid) == (base, 'loaded-model-A.gguf'), 'discovery handles /v1 suffix')

    result = await bridge.responses(FakeRequest({'model':'gguf@auto','stream':False,'input':'hello'}))
    check(isinstance(result, dict) and result.get('status') == 'completed', 'full Responses API request completed')
    check(Handler.requests[-1]['model'] == 'loaded-model-A.gguf', 'upstream received actual model ID')
    check(Handler.requests[-1]['max_tokens'] == 65536, 'upstream received GGUF max output')
    check(Handler.requests[-1]['min_p'] == 0, 'upstream received min_p 0')

    Handler.model = 'loaded-model-B.gguf'
    result = await bridge.responses(FakeRequest({'model':'gguf@auto','stream':False,'input':'hello again'}))
    check(result.get('status') == 'completed', 'response after hot swap completed')
    check(Handler.requests[-1]['model'] == 'loaded-model-B.gguf', 'hot swap is discovered on next request')

    Handler.models = [{'id':'A'}, {'id':'B'}]
    try:
        await bridge.discover_gguf_auto()
        raise AssertionError('Ambiguous multi-model endpoint should fail')
    except bridge.GGUFDiscoveryError as exc:
        check('multiple' in str(exc).lower(), 'ambiguous multi-model endpoint fails without guessing')
    Handler.models = []

    os.environ['GGUF_BASE_URL'] = f'http://127.0.0.1:{server.server_port + 1}'
    try:
        await bridge.discover_gguf_auto()
        raise AssertionError('Offline explicit endpoint should fail, not fall back')
    except bridge.GGUFDiscoveryError:
        check(True, 'offline explicit endpoint never silently falls back')
    os.environ['GGUF_BASE_URL'] = base + '/v1'

    Handler.auth = 'test-key'
    os.environ['GGUF_API_KEY'] = 'test-key'
    result = await bridge.responses(FakeRequest({'model':'gguf@auto','stream':False,'input':'key test'}))
    check(result.get('status') == 'completed', 'optional key authenticates discovery and chat')
    Handler.auth = None
    Handler.models = [{'id':'cold','status':{'value':'unloaded'}}, {'id':'hot','status':{'value':'loaded'}}]
    check((await bridge.discover_gguf_auto())[1] == 'hot', 'loaded status is preferred over unloaded')

try:
    asyncio.run(run())
finally:
    server.shutdown()
    server.server_close()
    for key, value in old.items():
        if value is None: os.environ.pop(key, None)
        else: os.environ[key] = value
print('[gguf-auto] PASS all')
