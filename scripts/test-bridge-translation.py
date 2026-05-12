#!/usr/bin/env python3
"""Bridge non-streaming translation test with fake DeepSeek server."""
import json, http.server, threading, os, socket, subprocess, sys, time, urllib.request

class FakeDS(http.server.BaseHTTPRequestHandler):
    last_body = None
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        FakeDS.last_body = body
        resp = {
            'id':'chatcmpl-fake','object':'chat.completion','created':123,
            'model':'deepseek-v4-flash',
            'choices':[{'index':0,'message':{'role':'assistant','content':'codeseeq-ok'},'finish_reason':'stop'}],
            'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}
        }
        self.send_response(200)
        self.send_header('Content-Type','application/json')
        self.end_headers()
        self.wfile.write(json.dumps(resp).encode())

def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]; s.close(); return p

ds_port = free_port(); br_port = free_port()

server = http.server.HTTPServer(('127.0.0.1', ds_port), FakeDS)
t = threading.Thread(target=server.serve_forever, daemon=True)
t.start()
time.sleep(0.2)

env = os.environ.copy()
env.update({'DEEPSEEK_API_KEY':'sk-test-fake-ds','DEEPSEEK_CHAT_URL':f'http://127.0.0.1:{ds_port}/chat/completions',
            'CODESEEQ_BRIDGE_PORT':str(br_port),'CODESEEQ_BRIDGE_HOST':'127.0.0.1'})

proc = subprocess.Popen([sys.executable,'bin/codeseeq-bridge.py'], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

for i in range(20):
    try:
        r = urllib.request.urlopen(f'http://127.0.0.1:{br_port}/health')
        if r.status == 200: break
    except: pass
    time.sleep(0.3)
else:
    proc.kill(); proc.wait(); print('FAIL: bridge not healthy'); sys.exit(1)

req = urllib.request.Request(f'http://127.0.0.1:{br_port}/v1/responses',
    data=json.dumps({'model':'deepseek@deepseek-v4-flash','stream':False,'input':'Return exactly: codeseeq-ok'}).encode(),
    headers={'Content-Type':'application/json','Authorization':'Bearer sk-test-fake-ds'})
try:
    body = json.loads(urllib.request.urlopen(req).read())
except Exception as e:
    proc.kill(); proc.wait(); print(f'FAIL: {e}'); sys.exit(1)

try:
    assert body.get('object') == 'response', f'object: {body.get("object")}'
    assert body.get('status') == 'completed', f'status: {body.get("status")}'
    assert 'codeseeq-ok' in body.get('output_text','')
    assert FakeDS.last_body is not None
    assert FakeDS.last_body['model'] == 'deepseek-v4-flash'
    assert FakeDS.last_body.get('stream') == False
    print('Non-streaming translation: PASS')
except AssertionError as e:
    print(f'FAIL: {e}'); sys.exit(1)
finally:
    proc.kill(); proc.wait(); server.shutdown()
