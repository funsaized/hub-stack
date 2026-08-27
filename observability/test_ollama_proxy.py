import json
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO

from observability import ollama_proxy


class OllamaProxyTest(unittest.TestCase):
    def test_records_completed_generation(self) -> None:
        metrics = ollama_proxy.Metrics()
        metrics.start()
        metrics.finish(
            model='model"name',
            endpoint="/api/generate",
            status=200,
            elapsed=2,
            ttft=1.5,
            final={
                "prompt_eval_count": 10,
                "prompt_eval_duration": 1_000_000_000,
                "eval_count": 20,
                "eval_duration": 2_000_000_000,
                "load_duration": 200_000_000,
                "total_duration": 3_200_000_000,
            },
        )
        output = metrics.render("ollama_up 1\n").decode()

        self.assertIn('ollama_requests_total{model="model\\\"name",endpoint="/api/generate",status="200"} 1', output)
        self.assertIn('ollama_generated_tokens_total{model="model\\\"name"} 20', output)
        self.assertIn('ollama_last_decode_tokens_per_second{model="model\\\"name"} 10', output)
        self.assertIn('ollama_queue_duration_seconds_count{model="model\\\"name",endpoint="/api/generate"} 1', output)
        self.assertIn('ollama_queue_duration_seconds_sum{model="model\\\"name",endpoint="/api/generate"} 0.3', output)
        self.assertIn("ollama_active_requests 0", output)

    def test_usage_only_response_records_volume_without_timing_metrics(self) -> None:
        metrics = ollama_proxy.Metrics()
        metrics.start()
        metrics.finish(
            model="test",
            endpoint="/v1/chat/completions",
            status=200,
            elapsed=2,
            ttft=0.1,
            final={"prompt_eval_count": 17, "eval_count": 2},
        )
        output = metrics.render("ollama_up 1\n").decode()

        self.assertIn('ollama_prompt_tokens_total{model="test"} 17', output)
        self.assertIn('ollama_generated_tokens_total{model="test"} 2', output)
        self.assertNotIn('ollama_prompt_evaluation_seconds_total{model="test"}', output)
        self.assertNotIn('ollama_generation_seconds_total{model="test"}', output)
        self.assertNotIn('ollama_last_decode_tokens_per_second{model="test"}', output)
        self.assertNotIn('ollama_last_prompt_tokens_per_second{model="test"}', output)
        self.assertNotIn('ollama_queue_duration_seconds_count{model="test"', output)

    def test_queue_is_wait_before_compute(self) -> None:
        metrics = ollama_proxy.Metrics()
        metrics.start()
        metrics.finish(
            model="test",
            endpoint="/api/generate",
            status=200,
            elapsed=11.5,
            ttft=9,
            final={
                "load_duration": 100_000_000,
                "prompt_eval_duration": 200_000_000,
                "eval_duration": 2_000_000_000,
            },
        )
        output = metrics.render("ollama_up 1\n").decode()

        self.assertIn('ollama_queue_duration_seconds_sum{model="test",endpoint="/api/generate"} 8.7', output)

    def test_extracts_generate_and_chat_text(self) -> None:
        self.assertEqual(ollama_proxy.event_text({"response": "hello"}), "hello")
        self.assertEqual(ollama_proxy.event_text({"message": {"content": "hello"}}), "hello")
        self.assertEqual(ollama_proxy.event_text({"message": {"thinking": "hmm"}}), "hmm")
        self.assertEqual(
            ollama_proxy.event_text({"choices": [{"delta": {"reasoning": "hmm"}}]}),
            "hmm",
        )

    def test_parses_openai_sse_usage(self) -> None:
        event = ollama_proxy.response_event(
            b'data: {"choices":[],"usage":{"prompt_tokens":17,"completion_tokens":2}}\n'
        )
        self.assertEqual(
            ollama_proxy.final_measurement(event or {}),
            {"prompt_eval_count": 17, "eval_count": 2},
        )
        self.assertIsNone(ollama_proxy.response_event(b"data: [DONE]\n"))

    def test_decodes_chunked_request_body(self) -> None:
        body = BytesIO(b"4\r\ntest\r\n3\r\n123\r\n0\r\nX-Test: yes\r\n\r\n")
        self.assertEqual(b"".join(ollama_proxy.chunked_body(body)), b"test123")

    def test_streams_and_measures_generation(self) -> None:
        class FakeOllama(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                final = (
                    b'{"done":true,"prompt_eval_count":4,"prompt_eval_duration":1000000000,'
                    b'"eval_count":8,"eval_duration":2000000000,"total_duration":3000000000}'
                )
                body = b'{"response":"hello","done":false}\n' + final + b"\n" if request["stream"] else final
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                pass

        upstream = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
        proxy = ThreadingHTTPServer(("127.0.0.1", 0), ollama_proxy.ProxyHandler)
        old_port, old_metrics = ollama_proxy.UPSTREAM_PORT, ollama_proxy.METRICS
        ollama_proxy.UPSTREAM_PORT = upstream.server_port
        ollama_proxy.METRICS = ollama_proxy.Metrics()
        threads = [threading.Thread(target=server.serve_forever) for server in (upstream, proxy)]
        for thread in threads:
            thread.start()
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{proxy.server_port}/api/generate",
                data=json.dumps({"model": "test", "stream": True}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request) as response:
                events = [json.loads(line) for line in response]
            output = ollama_proxy.METRICS.render("ollama_up 1\n").decode()

            self.assertTrue(events[-1]["done"])
            self.assertIn('ollama_generated_tokens_total{model="test"} 8', output)
            self.assertIn('ollama_time_to_first_token_seconds_count{model="test",endpoint="/api/generate"} 1', output)

            request = urllib.request.Request(
                f"http://127.0.0.1:{proxy.server_port}/api/generate",
                data=json.dumps({"model": "test", "stream": False}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request) as response:
                self.assertTrue(json.load(response)["done"])
            deadline = time.monotonic() + 1
            while 'ollama_generated_tokens_total{model="test"} 16' not in output:
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.01)
                output = ollama_proxy.METRICS.render("ollama_up 1\n").decode()
            self.assertIn('ollama_generated_tokens_total{model="test"} 16', output)
        finally:
            upstream.shutdown()
            proxy.shutdown()
            for thread in threads:
                thread.join()
            upstream.server_close()
            proxy.server_close()
            ollama_proxy.UPSTREAM_PORT, ollama_proxy.METRICS = old_port, old_metrics


if __name__ == "__main__":
    unittest.main()
