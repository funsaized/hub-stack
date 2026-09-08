import http.client
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from observability import activity, ollama_proxy


class ActivityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / 'history.sqlite3')
        self.store = activity.ActivityStore(self.path)

    def test_persistence_search_stats_and_restart(self):
        identifier = self.store.start('test', '/api/chat', 'agent', 'review', '{"messages":[{"content":"find bugs"}]}')
        self.assertEqual(self.store.search({})['stats']['running'], 1)
        self.store.finish(identifier, 200, 2.5, 0.4, {'prompt_eval_count': 5, 'eval_count': 9}, 'found a bug', False, None)
        pending = self.store.start('other', '/api/chat', 'webui', '', '{}')
        restored = activity.ActivityStore(self.path)
        self.assertEqual(restored.detail(pending)['state'], 'interrupted')
        result = restored.search({'q': ['FOUND A BUG'], 'source': ['agent']})
        self.assertEqual(result['stats']['requests'], 1)
        self.assertEqual(result['stats']['generated_tokens'], 9)
        self.assertEqual(result['rows'][0]['id'], identifier)
        self.assertNotIn('response', result['rows'][0])
        self.assertEqual(restored.detail(identifier)['request'], '{"messages":[{"content":"find bugs"}]}')
        self.assertEqual(restored.search({'q': ["' OR 1=1 --"]})['stats']['requests'], 0)

    def test_retention_keeps_running_and_recent_requests(self):
        with patch('observability.activity.time.time', return_value=time.time() - 40 * 86400):
            old = self.store.start('test', '/api/chat', 'a', '', '{}')
            running = self.store.start('test', '/api/chat', 'a', '', '{}')
        self.store.finish(old, 200, 1, None, None, '', False, None)
        self.store.start('test', '/api/chat', 'a', '', '{}')
        self.assertIsNone(self.store.detail(old))
        self.assertEqual(self.store.detail(running)['state'], 'running')

    def test_end_to_end_transcripts_and_failures(self):
        entered, release = threading.Event(), threading.Event()

        class Upstream(BaseHTTPRequestHandler):
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                mode = request.get('mode', 'native')
                if mode == 'wait':
                    entered.set()
                    release.wait(5)
                if mode == 'sse':
                    body = (b'data: {"choices":[{"delta":{"reasoning":"hmm","content":"hello","tool_calls":[{"id":"call1"}]}}]}\n\n'
                            b'data: {"choices":[],"usage":{"prompt_tokens":3,"completion_tokens":4}}\n\n'
                            b'data: [DONE]\n\n')
                elif mode == 'embedding':
                    body = json.dumps({'embeddings': [[0.1, 0.2]], 'prompt_eval_count': 3}, indent=2).encode()
                elif mode == 'error':
                    body = b'{"error":"model missing"}'
                elif mode == 'incomplete':
                    body = b'{"response":"partial","done":false}\n'
                else:
                    body = b'{"message":{"thinking":"hmm","content":"hello"},"done":false}\n{"done":true,"eval_count":4}\n'
                self.send_response(404 if mode == 'error' else 200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        proxy = ThreadingHTTPServer(('127.0.0.1', 0), ollama_proxy.ProxyHandler)
        for server in (upstream, proxy):
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
        self.addCleanup(release.set)

        def request(path, payload=None):
            connection = http.client.HTTPConnection('127.0.0.1', proxy.server_port, timeout=5)
            connection.request('POST' if payload else 'GET', path,
                               json.dumps(payload) if payload else None,
                               {'X-Hub-Source': 'test-agent', 'X-Hub-Purpose': 'verify capture'})
            response = connection.getresponse()
            status, body = response.status, response.read()
            connection.close()
            return status, body

        def completed(count):
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                rows = self.store.search({})['rows']
                if len(rows) == count and all(r['state'] != 'running' for r in rows):
                    return rows
                time.sleep(.01)
            self.fail('Invocations did not finish')

        with patch.object(ollama_proxy, 'UPSTREAM_PORT', upstream.server_port), patch.object(ollama_proxy, 'ACTIVITY', self.store):
            thread = threading.Thread(target=request, args=('/api/chat', {'model': 'test', 'mode': 'wait'}))
            thread.start()
            self.assertTrue(entered.wait(2))
            status, body = request('/activity/api/requests')
            self.assertEqual(json.loads(body)['stats']['running'], 1)
            release.set()
            thread.join(5)
            rows = completed(1)
            record = self.store.detail(rows[0]['id'])
            self.assertEqual(record['source'], 'test-agent')
            self.assertIn('thinking', record['response'])
            self.assertEqual(record['generated_tokens'], 4)
            status, body = request('/v1/chat/completions', {'model': 'test', 'mode': 'sse', 'stream': True})
            record = self.store.detail(completed(2)[0]['id'])
            self.assertEqual(record['response'].encode(), body)
            self.assertEqual(record['prompt_tokens'], 3)
            self.assertIn('tool_calls', record['response'])
            request('/api/embed', {'model': 'test', 'mode': 'embedding'})
            record = self.store.detail(completed(3)[0]['id'])
            self.assertEqual(json.loads(record['response'])['embeddings'], [[0.1, 0.2]])
            self.assertEqual(record['prompt_tokens'], 3)
            request('/api/chat', {'model': 'test', 'mode': 'error'})
            record = self.store.detail(completed(4)[0]['id'])
            self.assertEqual(record['state'], 'error')
            self.assertEqual(record['status'], 404)
            self.assertEqual(record['error'], 'model missing')
            request('/api/chat', {'model': 'test', 'mode': 'incomplete'})
            record = self.store.detail(completed(5)[0]['id'])
            self.assertEqual(record['state'], 'error')
            with patch.object(ollama_proxy, 'MAX_CAPTURE_BYTES', 10):
                _, body = request('/api/chat', {'model': 'test'})
            record = self.store.detail(completed(6)[0]['id'])
            self.assertEqual(len(record['response'].encode()), 10)
            self.assertTrue(record['truncated'])
            self.assertGreater(len(body), 10)
            with patch.object(self.store, 'start', side_effect=sqlite3.OperationalError('disk full')):
                status, body = request('/api/chat', {'model': 'test'})
                self.assertEqual(status, 200)
                self.assertIn(b'hello', body)
            self.assertEqual(request('/activity/')[0], 200)
            self.assertEqual(request('/activity/api/requests?offset=invalid')[0], 400)
            self.assertEqual(request('/activity/api/requests/missing')[0], 404)


if __name__ == '__main__':
    unittest.main()
