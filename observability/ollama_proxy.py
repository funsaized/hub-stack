#!/usr/bin/env python3
"""Transparent Ollama proxy with low-cardinality Prometheus metrics."""

import http.client
import json
import os
import threading
import time
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit


LISTEN_HOST = os.getenv("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.getenv("LISTEN_PORT", "11434"))
UPSTREAM_HOST = os.getenv("UPSTREAM_HOST", "127.0.0.1")
UPSTREAM_PORT = int(os.getenv("UPSTREAM_PORT", "11435"))
UPSTREAM_TIMEOUT = float(os.getenv("UPSTREAM_TIMEOUT", "600"))
MAX_REQUEST_BYTES = int(os.getenv("MAX_REQUEST_BYTES", str(256 * 1024 * 1024)))
MAX_METRIC_MODELS = int(os.getenv("MAX_METRIC_MODELS", "32"))
TRACKED_ENDPOINTS = {"/api/chat", "/api/generate", "/v1/chat/completions"}
HISTOGRAM_BUCKETS = (0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300)
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


def metric_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def event_text(event: dict[str, Any]) -> str:
    choices = event.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("delta") or choices[0].get("message")
        if isinstance(message, dict):
            return str(message.get("content") or message.get("reasoning") or "")
    message = event.get("message")
    if isinstance(message, dict):
        return str(message.get("content") or message.get("thinking") or "")
    return str(event.get("response") or event.get("thinking") or "")


def response_event(chunk: bytes) -> dict[str, Any] | None:
    payload = chunk.strip()
    if payload.startswith(b"data:"):
        payload = payload[5:].strip()
    if not payload or payload == b"[DONE]":
        return None
    try:
        event = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


def final_measurement(event: dict[str, Any]) -> dict[str, Any] | None:
    if event.get("done"):
        return event
    usage = event.get("usage")
    if isinstance(usage, dict):
        return {
            "prompt_eval_count": usage.get("prompt_tokens"),
            "eval_count": usage.get("completion_tokens"),
        }
    if event.get("object") == "chat.completion":
        return {}
    return None


class RequestBodyError(ValueError):
    pass


class ContentLengthReader:
    def __init__(self, source: Any, length: int) -> None:
        self.source = source
        self.remaining = length

    def read(self, size: int = -1) -> bytes:
        if not self.remaining:
            return b""
        chunk = self.source.read(min(size if size >= 0 else self.remaining, self.remaining))
        if not chunk:
            raise RequestBodyError("request body ended before Content-Length")
        self.remaining -= len(chunk)
        return chunk


def chunked_body(source: Any, maximum: int | None = None) -> Any:
    total = 0
    while True:
        line = source.readline(4096)
        if not line.endswith(b"\r\n"):
            raise RequestBodyError("invalid chunk header")
        try:
            size = int(line.split(b";", 1)[0], 16)
        except ValueError as error:
            raise RequestBodyError("invalid chunk size") from error
        if size == 0:
            while source.readline(4096) not in (b"\r\n", b""):
                pass
            return
        total += size
        if maximum is not None and total > maximum:
            raise RequestBodyError("request body too large")
        chunk = source.read(size)
        if len(chunk) != size or source.read(2) != b"\r\n":
            raise RequestBodyError("incomplete chunk")
        yield chunk


class Metrics:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active = 0
        self.models: set[str] = set()
        self.requests: dict[tuple[str, str, str], int] = defaultdict(int)
        self.prompt_tokens: dict[str, int] = defaultdict(int)
        self.generated_tokens: dict[str, int] = defaultdict(int)
        self.prompt_seconds: dict[str, float] = defaultdict(float)
        self.eval_seconds: dict[str, float] = defaultdict(float)
        self.load_seconds: dict[str, float] = defaultdict(float)
        self.last_decode_rate: dict[str, float] = {}
        self.last_prompt_rate: dict[str, float] = {}
        self.histograms: dict[str, dict[tuple[str, str], dict[str, Any]]] = {
            "ollama_request_duration_seconds": {},
            "ollama_time_to_first_token_seconds": {},
            "ollama_queue_duration_seconds": {},
        }

    def start(self) -> None:
        with self.lock:
            self.active += 1

    def finish(
        self,
        *,
        model: str,
        endpoint: str,
        status: int,
        elapsed: float,
        ttft: float | None,
        final: dict[str, Any] | None,
    ) -> None:
        with self.lock:
            self.active -= 1
            if model not in self.models:
                if len(self.models) < MAX_METRIC_MODELS:
                    self.models.add(model)
                else:
                    model = "other"
            self.requests[(model, endpoint, str(status))] += 1
            self._observe("ollama_request_duration_seconds", (model, endpoint), elapsed)
            if ttft is not None:
                self._observe("ollama_time_to_first_token_seconds", (model, endpoint), ttft)
            if not final:
                return
            ollama_seconds = float(final.get("total_duration") or 0) / 1e9
            if ollama_seconds:
                self._observe(
                    "ollama_queue_duration_seconds",
                    (model, endpoint),
                    max(elapsed - ollama_seconds, 0),
                )
            prompt_tokens = int(final.get("prompt_eval_count") or 0)
            generated_tokens = int(final.get("eval_count") or 0)
            prompt_seconds = float(final.get("prompt_eval_duration") or 0) / 1e9
            eval_seconds = float(final.get("eval_duration") or 0) / 1e9
            self.prompt_tokens[model] += prompt_tokens
            self.generated_tokens[model] += generated_tokens
            self.prompt_seconds[model] += prompt_seconds
            self.eval_seconds[model] += eval_seconds
            self.load_seconds[model] += float(final.get("load_duration") or 0) / 1e9
            if eval_seconds:
                self.last_decode_rate[model] = generated_tokens / eval_seconds
            if prompt_seconds:
                self.last_prompt_rate[model] = prompt_tokens / prompt_seconds

    def render(self, ollama_state: str) -> bytes:
        lines = [ollama_state.rstrip()]
        with self.lock:
            lines.extend(
                (
                    "# HELP ollama_active_requests Requests currently handled by the proxy.",
                    "# TYPE ollama_active_requests gauge",
                    f"ollama_active_requests {self.active}",
                    "# HELP ollama_requests_total Completed generation requests.",
                    "# TYPE ollama_requests_total counter",
                )
            )
            for labels, value in sorted(self.requests.items()):
                model, endpoint, status = map(metric_label, labels)
                lines.append(
                    f'ollama_requests_total{{model="{model}",endpoint="{endpoint}",status="{status}"}} {value}'
                )
            self._render_model_counter(lines, "ollama_prompt_tokens_total", self.prompt_tokens)
            self._render_model_counter(lines, "ollama_generated_tokens_total", self.generated_tokens)
            self._render_model_counter(lines, "ollama_prompt_evaluation_seconds_total", self.prompt_seconds)
            self._render_model_counter(lines, "ollama_generation_seconds_total", self.eval_seconds)
            self._render_model_counter(lines, "ollama_model_load_seconds_total", self.load_seconds)
            self._render_model_gauge(lines, "ollama_last_decode_tokens_per_second", self.last_decode_rate)
            self._render_model_gauge(lines, "ollama_last_prompt_tokens_per_second", self.last_prompt_rate)
            for name, values in self.histograms.items():
                lines.extend((f"# HELP {name} Observed {name.replace('_', ' ')}.", f"# TYPE {name} histogram"))
                for labels, histogram in sorted(values.items()):
                    model, endpoint = map(metric_label, labels)
                    label_text = f'model="{model}",endpoint="{endpoint}"'
                    for bucket, count in zip(HISTOGRAM_BUCKETS, histogram["buckets"], strict=True):
                        lines.append(f'{name}_bucket{{{label_text},le="{bucket:g}"}} {count}')
                    lines.append(f'{name}_bucket{{{label_text},le="+Inf"}} {histogram["count"]}')
                    lines.append(f'{name}_sum{{{label_text}}} {histogram["sum"]:.9g}')
                    lines.append(f'{name}_count{{{label_text}}} {histogram["count"]}')
        return ("\n".join(lines) + "\n").encode()

    def _observe(self, name: str, labels: tuple[str, str], value: float) -> None:
        histogram = self.histograms[name].setdefault(
            labels, {"buckets": [0] * len(HISTOGRAM_BUCKETS), "sum": 0.0, "count": 0}
        )
        for index, bucket in enumerate(HISTOGRAM_BUCKETS):
            if value <= bucket:
                histogram["buckets"][index] += 1
        histogram["sum"] += value
        histogram["count"] += 1

    @staticmethod
    def _render_model_counter(lines: list[str], name: str, values: dict[str, int | float]) -> None:
        lines.extend((f"# TYPE {name} counter",))
        lines.extend(f'{name}{{model="{metric_label(model)}"}} {value}' for model, value in sorted(values.items()))

    @staticmethod
    def _render_model_gauge(lines: list[str], name: str, values: dict[str, float]) -> None:
        lines.extend((f"# TYPE {name} gauge",))
        lines.extend(f'{name}{{model="{metric_label(model)}"}} {value:.9g}' for model, value in sorted(values.items()))


METRICS = Metrics()


def ollama_state_metrics() -> str:
    try:
        connection = http.client.HTTPConnection(UPSTREAM_HOST, UPSTREAM_PORT, timeout=3)
        connection.request("GET", "/api/version")
        response = connection.getresponse()
        version_data = json.loads(response.read())
        if response.status != 200:
            raise RuntimeError(f"version endpoint returned {response.status}")
        version = metric_label(str(version_data.get("version") or "unknown"))
        connection.close()

        connection = http.client.HTTPConnection(UPSTREAM_HOST, UPSTREAM_PORT, timeout=3)
        connection.request("GET", "/api/ps")
        response = connection.getresponse()
        models = json.loads(response.read()).get("models", [])
        if response.status != 200:
            raise RuntimeError(f"ps endpoint returned {response.status}")
        connection.close()

        lines = [
            "# TYPE ollama_up gauge",
            "ollama_up 1",
            "# TYPE ollama_version_info gauge",
            f'ollama_version_info{{version="{version}"}} 1',
        ]
        lines.extend(
            (
                "# TYPE ollama_model_loaded gauge",
                "# TYPE ollama_model_size_bytes gauge",
                "# TYPE ollama_model_vram_bytes gauge",
                "# TYPE ollama_model_context_length gauge",
            )
        )
        for model in models:
            name = metric_label(str(model.get("name") or "unknown"))
            lines.extend(
                (
                    f'ollama_model_loaded{{model="{name}"}} 1',
                    f'ollama_model_size_bytes{{model="{name}"}} {int(model.get("size") or 0)}',
                    f'ollama_model_vram_bytes{{model="{name}"}} {int(model.get("size_vram") or 0)}',
                    f'ollama_model_context_length{{model="{name}"}} {int(model.get("context_length") or 0)}',
                )
            )
    except (
        AttributeError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        http.client.HTTPException,
    ):
        lines = ["# TYPE ollama_up gauge", "ollama_up 0"]
    return "\n".join(lines) + "\n"


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ollama-metrics-proxy/1"

    def do_GET(self) -> None:
        if urlsplit(self.path).path == "/metrics":
            body = METRICS.render(ollama_state_metrics())
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.proxy()

    def do_POST(self) -> None:
        self.proxy()

    def do_DELETE(self) -> None:
        self.proxy()

    def do_HEAD(self) -> None:
        self.proxy()

    def proxy(self) -> None:
        endpoint = urlsplit(self.path).path
        tracked = endpoint in TRACKED_ENDPOINTS
        try:
            content_length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self.send_error(400, "invalid Content-Length")
            return
        if content_length < 0:
            self.send_error(400, "invalid Content-Length")
            return
        if tracked and content_length > MAX_REQUEST_BYTES:
            self.send_error(413, "request body too large")
            return
        transfer_encoding = self.headers.get("Transfer-Encoding", "").lower()
        if transfer_encoding not in ("", "chunked"):
            self.send_error(501, "unsupported Transfer-Encoding")
            return
        encode_chunked = transfer_encoding == "chunked"
        try:
            if encode_chunked:
                chunks = chunked_body(self.rfile, MAX_REQUEST_BYTES if tracked else None)
                body = b"".join(chunks) if tracked else chunks
            elif tracked:
                body = ContentLengthReader(self.rfile, content_length).read() if content_length else None
            else:
                body = ContentLengthReader(self.rfile, content_length) if content_length else None
        except RequestBodyError as error:
            self.send_error(413 if "too large" in str(error) else 400, str(error))
            return
        payload: dict[str, Any] = {}
        if isinstance(body, bytes):
            try:
                candidate = json.loads(body)
                payload = candidate if isinstance(candidate, dict) else {}
            except json.JSONDecodeError:
                pass
        model = str(payload.get("model") or "unknown")
        started = time.monotonic()
        first_token_at: float | None = None
        final: dict[str, Any] | None = None
        status = 502
        headers_sent = False
        if tracked:
            METRICS.start()
        try:
            upstream = http.client.HTTPConnection(UPSTREAM_HOST, UPSTREAM_PORT, timeout=UPSTREAM_TIMEOUT)
            headers = {
                name: value
                for name, value in self.headers.items()
                if name.lower() not in HOP_BY_HOP_HEADERS and name.lower() != "host"
            }
            headers["Accept-Encoding"] = "identity"
            upstream.request(
                self.command,
                self.path,
                body=body,
                headers=headers,
                encode_chunked=encode_chunked,
            )
            response = upstream.getresponse()
            status = response.status
            self.send_response(response.status, response.reason)
            for name, value in response.getheaders():
                if name.lower() not in HOP_BY_HOP_HEADERS:
                    self.send_header(name, value)
            self.send_header("Connection", "close")
            self.end_headers()
            headers_sent = True

            if tracked:
                while chunk := response.readline():
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    event = response_event(chunk)
                    if event is None:
                        continue
                    if first_token_at is None and event_text(event):
                        first_token_at = time.monotonic()
                    measurement = final_measurement(event)
                    if measurement is not None:
                        final = measurement
            else:
                while chunk := response.read(64 * 1024):
                    self.wfile.write(chunk)
            upstream.close()
        except (BrokenPipeError, ConnectionResetError):
            status = 499
        except RequestBodyError as error:
            status = 400
            if not headers_sent:
                self.send_error(400, str(error))
        except (OSError, http.client.HTTPException) as error:
            status = 502
            if not headers_sent:
                self.send_error(502, f"Ollama upstream unavailable: {error}")
        finally:
            self.close_connection = True
            if tracked:
                elapsed = time.monotonic() - started
                METRICS.finish(
                    model=model,
                    endpoint=endpoint,
                    status=status,
                    elapsed=elapsed,
                    ttft=None if first_token_at is None else first_token_at - started,
                    final=final,
                )
                print(
                    json.dumps(
                        {
                            "event": "ollama_request",
                            "model": model,
                            "endpoint": endpoint,
                            "status": status,
                            "duration_seconds": round(elapsed, 6),
                            "prompt_tokens": int((final or {}).get("prompt_eval_count") or 0),
                            "generated_tokens": int((final or {}).get("eval_count") or 0),
                        },
                        separators=(",", ":"),
                    ),
                    flush=True,
                )

    def log_message(self, format: str, *args: Any) -> None:
        if urlsplit(self.path).path != "/metrics":
            print(json.dumps({"event": "proxy_access", "message": format % args}, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    print(
        json.dumps(
            {
                "event": "proxy_started",
                "listen": f"{LISTEN_HOST}:{LISTEN_PORT}",
                "upstream": f"{UPSTREAM_HOST}:{UPSTREAM_PORT}",
            },
            separators=(",", ":"),
        ),
        flush=True,
    )
    ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), ProxyHandler).serve_forever()
