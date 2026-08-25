#!/usr/bin/env python3
"""Reproducible end-to-end benchmark for the local Ollama hub."""

import argparse
import concurrent.futures
import datetime
import decimal
import json
import math
import os
import platform
import re
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


DEFAULT_OLLAMA = "http://127.0.0.1:11434"
DEFAULT_PROMETHEUS = "http://127.0.0.1:9090"
DEFAULT_MODEL = "qwen3.5:9b"
SCHEMA_VERSION = 2
NANOSECONDS = 1_000_000_000


@dataclass(frozen=True)
class QualityCase:
    case_id: str
    category: str
    prompt: str
    expected: Any
    scorer: str = "normalized"


QUALITY_CASES = (
    QualityCase(
        "arithmetic-multiplication",
        "arithmetic",
        "Compute 739 multiplied by 864. Reply with the number only.",
        "638496",
        "number",
    ),
    QualityCase(
        "arithmetic-percentage",
        "arithmetic",
        "What is 17 percent of 350? Reply with the number only.",
        "59.5",
        "number",
    ),
    QualityCase(
        "arithmetic-order-of-operations",
        "arithmetic",
        "Compute (18 + 6) / 3. Reply with the number only.",
        "8",
        "number",
    ),
    QualityCase(
        "arithmetic-word-problem",
        "arithmetic",
        "A box has 6 rows of 8 bolts. Thirteen bolts are removed. How many remain? Reply with the number only.",
        "35",
        "number",
    ),
    QualityCase(
        "reasoning-sequence",
        "reasoning",
        "Continue the sequence 2, 6, 12, 20, 30. Reply with the next number only.",
        "42",
        "number",
    ),
    QualityCase(
        "reasoning-snail",
        "reasoning",
        "A snail climbs 3 meters by day and slips 2 meters by night in a 10-meter well. Reply only with the escape day number.",
        "8",
        "number",
    ),
    QualityCase(
        "reasoning-syllogism",
        "reasoning",
        "All norps are flims. No flims are daxes. Can any norp be a dax? Reply only yes or no.",
        "no",
    ),
    QualityCase(
        "reasoning-ordering",
        "reasoning",
        "Mira is taller than Jo. Jo is taller than Eli. Who is shortest? Reply with the name only.",
        "Eli",
    ),
    QualityCase(
        "reasoning-converse",
        "reasoning",
        "If the server is down, the alert is red. The alert is red. Must the server be down? Reply only yes or no.",
        "no",
    ),
    QualityCase(
        "knowledge-capital",
        "knowledge",
        "What is the capital of Australia? Reply with the city only.",
        "Canberra",
    ),
    QualityCase(
        "knowledge-chemistry",
        "knowledge",
        "What is the chemical symbol for tungsten? Reply with the symbol only.",
        "W",
    ),
    QualityCase(
        "knowledge-literature",
        "knowledge",
        "Who wrote Pride and Prejudice? Reply with the author name only.",
        "Jane Austen",
    ),
    QualityCase(
        "knowledge-astronomy",
        "knowledge",
        "What is the largest planet in the Solar System? Reply with the planet only.",
        "Jupiter",
    ),
    QualityCase(
        "instruction-json",
        "instruction_following",
        'Reply with exactly this JSON object and nothing else: {"status":"ok","sum":42}',
        {"status": "ok", "sum": 42},
        "json",
    ),
    QualityCase(
        "instruction-sort",
        "instruction_following",
        "Sort pear, apple, banana alphabetically. Reply as comma-separated lowercase words with no spaces.",
        "apple,banana,pear",
        "exact",
    ),
    QualityCase(
        "instruction-uppercase",
        "instruction_following",
        "Write the words local model in uppercase. Reply with those two words only.",
        "LOCAL MODEL",
        "exact",
    ),
    QualityCase(
        "extraction-email",
        "extraction",
        "Extract the email address from: Contact Ada at ada.lovelace@example.org before Friday. Reply with the address only.",
        "ada.lovelace@example.org",
    ),
    QualityCase(
        "extraction-id",
        "extraction",
        "Extract the ticket ID from: Region=west; Ticket=HUB-2047; Priority=low. Reply with the ID only.",
        "HUB-2047",
    ),
    QualityCase(
        "code-python",
        "code_understanding",
        "What does this Python expression evaluate to: sum(i * i for i in range(5))? Reply with the number only.",
        "30",
        "number",
    ),
    QualityCase(
        "code-javascript",
        "code_understanding",
        "What does this JavaScript expression evaluate to: [1, 2, 3].map(x => x * 2).at(-1)? Reply with the number only.",
        "6",
        "number",
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark Ollama accuracy, latency, throughput, load behavior, and resources."
    )
    parser.add_argument("--model", default=os.getenv("BENCHMARK_MODEL", DEFAULT_MODEL))
    parser.add_argument(
        "--ollama-url", default=os.getenv("OLLAMA_URL", DEFAULT_OLLAMA)
    )
    parser.add_argument(
        "--prometheus-url",
        default=os.getenv("PROMETHEUS_URL", DEFAULT_PROMETHEUS),
    )
    parser.add_argument("--context-length", type=int, default=8192)
    parser.add_argument("--quality-runs", type=int, default=1)
    parser.add_argument("--warm-runs", type=int, default=5)
    parser.add_argument("--sustained-runs", type=int, default=8)
    parser.add_argument("--output-tokens", type=int, default=256)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--requests-per-client", type=int, default=2)
    parser.add_argument(
        "--metrics-settle-seconds",
        type=float,
        default=16,
        help="Wait for the final Prometheus scrape after load testing.",
    )
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--output", help="Also write the JSON report to this path.")
    parser.add_argument("--skip-quality", action="store_true")
    parser.add_argument("--skip-context", action="store_true")
    parser.add_argument("--skip-concurrency", action="store_true")
    parser.add_argument("--skip-resources", action="store_true")
    args = parser.parse_args()

    positive = (
        "context_length",
        "quality_runs",
        "warm_runs",
        "sustained_runs",
        "output_tokens",
        "concurrency",
        "requests_per_client",
        "timeout",
    )
    for name in positive:
        if getattr(args, name) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be greater than zero")
    if args.metrics_settle_seconds < 0:
        parser.error("--metrics-settle-seconds cannot be negative")
    args.ollama_url = args.ollama_url.rstrip("/")
    args.prometheus_url = args.prometheus_url.rstrip("/")
    return args


def log(message: str) -> None:
    print(f"[benchmark] {message}", file=sys.stderr, flush=True)


def request_json(url: str, payload: Any = None, timeout: float = 300) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"request failed for {url}: HTTP {error.code}: {detail or error.reason}"
        ) from error
    except (urllib.error.URLError, json.JSONDecodeError) as error:
        raise RuntimeError(f"request failed for {url}: {error}") from error


def generate(
    args: argparse.Namespace,
    prompt: str,
    num_predict: int,
    *,
    keep_alive: str | int = "30m",
) -> dict[str, Any]:
    payload = {
        "model": args.model,
        "prompt": prompt,
        "stream": True,
        "think": False,
        "keep_alive": keep_alive,
        "options": {
            "num_ctx": args.context_length,
            "num_predict": num_predict,
            "temperature": 0,
            "seed": 42,
        },
    }
    request = urllib.request.Request(
        f"{args.ollama_url}/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/x-ndjson",
        },
    )
    started = time.perf_counter()
    first_token_at = None
    chunks: list[str] = []
    final: dict[str, Any] = {}
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            for raw_line in response:
                if not raw_line.strip():
                    continue
                event = json.loads(raw_line)
                if event.get("error"):
                    raise RuntimeError(f"Ollama generation error: {event['error']}")
                text = event.get("response", "")
                if text and first_token_at is None:
                    first_token_at = time.perf_counter()
                chunks.append(text)
                if event.get("done"):
                    final = event
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"generation failed for {args.model}: HTTP {error.code}: "
            f"{detail or error.reason}"
        ) from error
    except (urllib.error.URLError, json.JSONDecodeError) as error:
        raise RuntimeError(f"generation failed for {args.model}: {error}") from error

    finished = time.perf_counter()
    if not final:
        raise RuntimeError("Ollama stream ended without a final timing event")
    wall_seconds = finished - started
    ttft_seconds = (first_token_at or finished) - started
    output_tokens = int(final.get("eval_count", 0))
    decode_seconds = nanoseconds(final.get("eval_duration"))
    prompt_tokens = int(final.get("prompt_eval_count", 0))
    prompt_seconds = nanoseconds(final.get("prompt_eval_duration"))
    post_first_token_seconds = max(finished - (first_token_at or finished), 0)

    return {
        "response": "".join(chunks).strip(),
        "wall_seconds": wall_seconds,
        "time_to_first_token_seconds": ttft_seconds,
        "load_seconds": nanoseconds(final.get("load_duration")),
        "ollama_total_seconds": nanoseconds(final.get("total_duration")),
        "prompt_tokens": prompt_tokens,
        "prompt_seconds": prompt_seconds,
        "prompt_tokens_per_second": rate(prompt_tokens, prompt_seconds),
        "output_tokens": output_tokens,
        "decode_seconds": decode_seconds,
        "decode_tokens_per_second": rate(output_tokens, decode_seconds),
        "wall_tokens_per_second_after_first_token": rate(
            max(output_tokens - 1, 0), post_first_token_seconds
        ),
        "end_to_end_tokens_per_second": rate(output_tokens, wall_seconds),
        "done_reason": final.get("done_reason"),
    }


def nanoseconds(value: Any) -> float:
    return float(value or 0) / NANOSECONDS


def rate(count: int | float, seconds: float) -> float | None:
    return count / seconds if seconds > 0 else None


def percentile(values: list[float], percent: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percent / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def summarize(values: list[float | None]) -> dict[str, float | int | None]:
    clean = [float(value) for value in values if value is not None]
    if not clean:
        return {"count": 0, "min": None, "mean": None, "median": None, "p95": None, "max": None}
    result: dict[str, float | int | None] = {
        "count": len(clean),
        "min": min(clean),
        "mean": statistics.fmean(clean),
        "median": statistics.median(clean),
        "p95": percentile(clean, 95),
        "max": max(clean),
    }
    if len(clean) > 1:
        result["standard_deviation"] = statistics.stdev(clean)
        result["coefficient_of_variation_percent"] = (
            statistics.stdev(clean) / statistics.fmean(clean) * 100
            if statistics.fmean(clean)
            else None
        )
    return result


def normalized_answer(value: str) -> str:
    answer = re.sub(r"\s+", " ", value.strip()).casefold()
    answer = re.sub(r"^answer\s*:\s*", "", answer)
    answer = answer.strip(" \t\r\n\"'")
    return answer[:-1] if answer.endswith(".") else answer


def score_answer(case: QualityCase, actual: str) -> tuple[bool, str | None]:
    if case.scorer == "exact":
        return actual.strip() == case.expected, None
    if case.scorer == "normalized":
        return normalized_answer(actual) == normalized_answer(str(case.expected)), None
    if case.scorer == "number":
        candidate = actual.strip().replace(",", "")
        match = re.fullmatch(
            r"(?:answer\s*:\s*)?([-+]?\d+(?:\.\d+)?)[.!]?",
            candidate,
            re.IGNORECASE,
        )
        if not match:
            return False, "response was not solely a numeric answer"
        try:
            return decimal.Decimal(match.group(1)) == decimal.Decimal(case.expected), None
        except decimal.InvalidOperation:
            return False, "response was not a valid decimal number"
    if case.scorer == "json":
        try:
            return json.loads(actual) == case.expected, None
        except json.JSONDecodeError as error:
            return False, f"invalid JSON: {error.msg}"
    raise ValueError(f"unknown scorer: {case.scorer}")


def public_measurement(result: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in result.items() if key != "response"}


def benchmark_quality(args: argparse.Namespace) -> dict[str, Any]:
    attempts = []
    categories: dict[str, list[bool]] = {}
    for run in range(1, args.quality_runs + 1):
        for index, case in enumerate(QUALITY_CASES, 1):
            log(f"quality run {run}/{args.quality_runs}, case {index}/{len(QUALITY_CASES)}")
            result = generate(args, case.prompt, 64)
            passed, detail = score_answer(case, result["response"])
            categories.setdefault(case.category, []).append(passed)
            attempts.append(
                {
                    "run": run,
                    "id": case.case_id,
                    "category": case.category,
                    "passed": passed,
                    "scorer": case.scorer,
                    "expected": case.expected,
                    "actual": result["response"],
                    "failure_detail": detail,
                    "measurement": public_measurement(result),
                }
            )
    passed = sum(attempt["passed"] for attempt in attempts)
    return {
        "method": "deterministic exact, normalized, numeric, and JSON scoring; no LLM judge",
        "passed": passed,
        "total": len(attempts),
        "accuracy_percent": passed / len(attempts) * 100,
        "categories": {
            category: {
                "passed": sum(results),
                "total": len(results),
                "accuracy_percent": sum(results) / len(results) * 100,
            }
            for category, results in categories.items()
        },
        "attempts": attempts,
    }


def unload_model(args: argparse.Namespace) -> bool:
    request_json(
        f"{args.ollama_url}/api/generate",
        {"model": args.model, "prompt": "", "stream": False, "keep_alive": 0},
        args.timeout,
    )
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        models = request_json(f"{args.ollama_url}/api/ps", timeout=args.timeout).get(
            "models", []
        )
        if not any(model.get("name") == args.model for model in models):
            return True
        time.sleep(0.25)
    return False


def benchmark_cold_start(args: argparse.Namespace) -> dict[str, Any]:
    log("unloading model for cold-start measurement")
    unloaded = unload_model(args)
    result = generate(args, "Reply with OK only.", 8)
    return {"model_was_confirmed_unloaded": unloaded, **public_measurement(result)}


def context_prompt(filler_word_count: int, needle_fraction: float, code: str) -> str:
    prefix = (
        "Read the archive below. One record contains an authorization code. "
        "After the archive, return that code only.\n\n"
    )
    suffix = "\n\nWhat is the authorization code? Reply with the code only."
    filler_words = max(filler_word_count - 60, 1)
    words = ("ledger archive neutral record " * math.ceil(filler_words / 4)).split()
    insert_at = int(len(words) * needle_fraction)
    words[insert_at:insert_at] = [
        "IMPORTANT",
        "RECORD:",
        "the",
        "authorization",
        "code",
        "is",
        code,
    ]
    return prefix + " ".join(words) + suffix


def benchmark_context(args: argparse.Namespace) -> dict[str, Any]:
    requested_cases = (
        (512, 0.10, "KESTREL-512", 0.15),
        (2048, 0.50, "ORCHID-2048", 0.40),
        (6144, 0.90, "SUMMIT-6144", 0.70),
    )
    cases = []
    for requested_words, position, code, context_fraction in requested_cases:
        target = min(requested_words, int(args.context_length * context_fraction))
        if target >= 128 and target not in {item[0] for item in cases}:
            cases.append((target, position, code))
    results = []
    for index, (target, position, expected) in enumerate(cases, 1):
        if target < 128:
            continue
        log(f"context case {index}/{len(cases)} ({target} synthetic filler words)")
        result = generate(args, context_prompt(target, position, expected), 32)
        passed = normalized_answer(result["response"]) == normalized_answer(expected)
        possible_truncation = (
            result["prompt_tokens"] + result["output_tokens"] >= args.context_length
        )
        results.append(
            {
                "target_filler_words": target,
                "actual_prompt_tokens": result["prompt_tokens"],
                "needle_position_percent": position * 100,
                "expected": expected,
                "actual": result["response"],
                "passed": passed,
                "possible_context_truncation": possible_truncation,
                "measurement": public_measurement(result),
            }
        )
    passed = sum(item["passed"] for item in results)
    return {
        "method": "synthetic needle retrieval at increasing filler word counts; actual Ollama token counts are reported",
        "passed": passed,
        "total": len(results),
        "accuracy_percent": passed / len(results) * 100 if results else None,
        "cases": results,
    }


THROUGHPUT_PROMPT = (
    "Write at least 500 words explaining why full GPU offload improves local "
    "LLM inference performance. Use continuous prose, not a list, and keep "
    "writing until the requested length is reached."
)


def summarize_generations(results: list[dict[str, Any]]) -> dict[str, Any]:
    fields = (
        "wall_seconds",
        "time_to_first_token_seconds",
        "prompt_tokens_per_second",
        "decode_tokens_per_second",
        "wall_tokens_per_second_after_first_token",
        "end_to_end_tokens_per_second",
    )
    return {
        "requests": len(results),
        "total_prompt_tokens": sum(result["prompt_tokens"] for result in results),
        "total_output_tokens": sum(result["output_tokens"] for result in results),
        "length_limited_requests": sum(
            result["done_reason"] == "length" for result in results
        ),
        "distributions": {
            field: summarize([result[field] for result in results]) for field in fields
        },
        "runs": [public_measurement(result) for result in results],
    }


def benchmark_warm(args: argparse.Namespace) -> dict[str, Any]:
    results = []
    for index in range(args.warm_runs):
        log(f"warm generation {index + 1}/{args.warm_runs}")
        results.append(
            generate(
                args,
                f"Warm benchmark run {index + 1}. {THROUGHPUT_PROMPT}",
                args.output_tokens,
            )
        )
    return summarize_generations(results)


def benchmark_sustained(args: argparse.Namespace) -> tuple[dict[str, Any], float, float]:
    results = []
    started_epoch = time.time()
    started = time.perf_counter()
    for index in range(args.sustained_runs):
        log(f"sustained generation {index + 1}/{args.sustained_runs}")
        results.append(
            generate(
                args,
                f"Sustained benchmark run {index + 1}. {THROUGHPUT_PROMPT}",
                args.output_tokens,
            )
        )
    elapsed = time.perf_counter() - started
    ended_epoch = time.time()
    report = summarize_generations(results)
    report["wall_seconds"] = elapsed
    report["aggregate_output_tokens_per_second"] = rate(
        report["total_output_tokens"], elapsed
    )
    speeds = [
        result["decode_tokens_per_second"]
        for result in results
        if result["decode_tokens_per_second"] is not None
    ]
    if not speeds:
        report["decode_speed_drift_percent"] = None
        return report, started_epoch, ended_epoch
    midpoint = max(len(speeds) // 2, 1)
    first = statistics.fmean(speeds[:midpoint])
    last = statistics.fmean(speeds[midpoint:]) if speeds[midpoint:] else first
    report["decode_speed_drift_percent"] = (last - first) / first * 100 if first else None
    return report, started_epoch, ended_epoch


def benchmark_concurrency(args: argparse.Namespace) -> tuple[dict[str, Any], float]:
    total_requests = args.concurrency * args.requests_per_client
    barrier = threading.Barrier(args.concurrency, timeout=args.timeout)

    def worker(client: int) -> list[dict[str, Any]]:
        barrier.wait()
        return [
            generate(
                args,
                f"{THROUGHPUT_PROMPT}\nRequest marker: client-{client}-run-{run}.",
                args.output_tokens,
            )
            for run in range(args.requests_per_client)
        ]

    log(f"concurrent load: {args.concurrency} clients, {total_requests} requests")
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=args.concurrency
    ) as executor:
        nested = list(executor.map(worker, range(args.concurrency)))
    elapsed = time.perf_counter() - started
    results = [result for client_results in nested for result in client_results]
    report = summarize_generations(results)
    report.update(
        {
            "clients": args.concurrency,
            "requests_per_client": args.requests_per_client,
            "wall_seconds": elapsed,
            "requests_per_second": rate(total_requests, elapsed),
            "aggregate_output_tokens_per_second": rate(
                report["total_output_tokens"], elapsed
            ),
        }
    )
    return report, elapsed


def prometheus_values(
    args: argparse.Namespace, query: str, start: float, end: float
) -> list[float]:
    params = urllib.parse.urlencode(
        {"query": query, "start": start, "end": end, "step": 1}
    )
    response = request_json(
        f"{args.prometheus_url}/api/v1/query_range?{params}", timeout=args.timeout
    )
    if response.get("status") != "success":
        raise RuntimeError(f"Prometheus query failed: {query}")
    return [
        float(sample[1])
        for series in response["data"]["result"]
        for sample in series["values"]
        if math.isfinite(float(sample[1]))
    ]


def benchmark_resources(
    args: argparse.Namespace, start: float, end: float
) -> dict[str, Any]:
    queries = {
        "gpu_utilization_percent": ("nvidia_smi_utilization_gpu_ratio", 100),
        "vram_used_gib": (
            "nvidia_smi_memory_used_bytes / 1024 / 1024 / 1024",
            1,
        ),
        "gpu_temperature_c": ("nvidia_smi_temperature_gpu", 1),
        "gpu_power_w": ("nvidia_smi_power_draw_watts", 1),
        "gpu_power_limit_w": ("nvidia_smi_power_limit_watts", 1),
        "gpu_graphics_clock_mhz": (
            "nvidia_smi_clocks_current_graphics_clock_hz / 1000000",
            1,
        ),
        "host_cpu_busy_percent": (
            '1 - avg(rate(node_cpu_seconds_total{mode="idle"}[30s]))',
            100,
        ),
        "host_memory_used_percent": (
            "1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)",
            100,
        ),
    }
    metrics: dict[str, Any] = {}
    missing = []
    for name, (query, scale) in queries.items():
        values = [
            value * scale for value in prometheus_values(args, query, start, end)
        ]
        if values:
            metrics[name] = summarize(values)
        else:
            metrics[name] = None
            missing.append(name)
    average_power = (metrics.get("gpu_power_w") or {}).get("mean")
    return {
        "available": bool(metrics) and len(missing) < len(metrics),
        "window_seconds": end - start,
        "sample_note": "Prometheus values are one-second query steps over 15-second scrapes.",
        "estimated_total_gpu_energy_wh": (
            average_power * (end - start) / 3600 if average_power is not None else None
        ),
        "missing_metrics": missing,
        "metrics": metrics,
    }


def command_output(command: list[str], cwd: str | None = None) -> str | None:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except (FileNotFoundError, subprocess.SubprocessError):
        return None


def environment_metadata(args: argparse.Namespace) -> dict[str, Any]:
    tags = request_json(f"{args.ollama_url}/api/tags", timeout=args.timeout)
    model = next(
        (item for item in tags.get("models", []) if item.get("name") == args.model),
        None,
    )
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    memory_gib = None
    try:
        with open("/proc/meminfo", encoding="utf-8") as meminfo:
            match = re.search(r"^MemTotal:\s+(\d+) kB$", meminfo.read(), re.MULTILINE)
            if match:
                memory_gib = int(match.group(1)) / 1024 / 1024
    except OSError:
        pass
    return {
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "benchmark_schema_version": SCHEMA_VERSION,
        "benchmark_git_revision": command_output(
            ["git", "rev-parse", "--short", "HEAD"], repo_root
        ),
        "benchmark_worktree_dirty": bool(
            command_output(["git", "status", "--short"], repo_root)
        ),
        "ollama_version": request_json(
            f"{args.ollama_url}/api/version", timeout=args.timeout
        ).get("version"),
        "model": model,
        "host": {
            "hostname": platform.node(),
            "operating_system": platform.platform(),
            "architecture": platform.machine(),
            "logical_cpus": os.cpu_count(),
            "memory_gib": memory_gib,
            "python": platform.python_version(),
            "gpu": command_output(
                [
                    "nvidia-smi",
                    "--query-gpu=name,driver_version,memory.total,power.limit",
                    "--format=csv,noheader,nounits",
                ]
            ),
            "ollama_service_environment": command_output(
                ["systemctl", "show", "ollama", "--property=Environment", "--value"]
            ),
        },
    }


def deployment_state(args: argparse.Namespace) -> dict[str, Any]:
    models = request_json(f"{args.ollama_url}/api/ps", timeout=args.timeout).get(
        "models", []
    )
    running = next(
        (item for item in models if item.get("name") == args.model), None
    )
    if not running:
        return {"model_loaded": False}
    size = running.get("size", 0)
    size_vram = running.get("size_vram", 0)
    return {
        "model_loaded": True,
        "context_length": running.get("context_length"),
        "model_size_bytes": size,
        "vram_model_bytes": size_vram,
        "vram_offload_percent": size_vram / size * 100 if size else None,
        "full_gpu_offload": bool(size and size_vram == size),
    }


def rounded(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 3)
    if isinstance(value, dict):
        return {key: rounded(item) for key, item in value.items()}
    if isinstance(value, list):
        return [rounded(item) for item in value]
    return value


def make_summary(report: dict[str, Any]) -> dict[str, Any]:
    accuracy = report.get("accuracy") or {}
    context = report.get("context_scaling") or {}
    warm = report["warm_generation"]["distributions"]
    sustained = report["sustained_load"]
    concurrent = report.get("concurrency") or {}
    return {
        "functional_accuracy_percent": accuracy.get("accuracy_percent"),
        "context_retrieval_accuracy_percent": context.get("accuracy_percent"),
        "cold_model_load_seconds": (
            report["cold_start"]["load_seconds"]
            if report["cold_start"]["model_was_confirmed_unloaded"]
            else None
        ),
        "cold_request_wall_seconds": (
            report["cold_start"]["wall_seconds"]
            if report["cold_start"]["model_was_confirmed_unloaded"]
            else None
        ),
        "warm_time_to_first_token_median_seconds": warm[
            "time_to_first_token_seconds"
        ]["median"],
        "warm_decode_tokens_per_second_median": warm[
            "decode_tokens_per_second"
        ]["median"],
        "warm_decode_tokens_per_second_p95": warm[
            "decode_tokens_per_second"
        ]["p95"],
        "sustained_aggregate_tokens_per_second": sustained[
            "aggregate_output_tokens_per_second"
        ],
        "sustained_decode_speed_drift_percent": sustained[
            "decode_speed_drift_percent"
        ],
        "concurrent_aggregate_tokens_per_second": concurrent.get(
            "aggregate_output_tokens_per_second"
        ),
        "concurrent_latency_p95_seconds": (
            concurrent.get("distributions", {}).get("wall_seconds", {}).get("p95")
        ),
        "full_gpu_offload": report["deployment"].get("full_gpu_offload"),
    }


def main() -> int:
    args = parse_args()
    benchmark_started = time.perf_counter()
    log(f"benchmarking {args.model} at {args.ollama_url}")
    environment = environment_metadata(args)
    if environment["model"] is None:
        raise RuntimeError(f"model is not installed: {args.model}")

    cold_start = benchmark_cold_start(args)
    accuracy = None if args.skip_quality else benchmark_quality(args)
    context = None if args.skip_context else benchmark_context(args)
    warm = benchmark_warm(args)
    sustained, load_start, load_end = benchmark_sustained(args)

    concurrency = None
    if not args.skip_concurrency:
        concurrency, _ = benchmark_concurrency(args)
        load_end = time.time()

    resources: dict[str, Any] = {"available": False, "reason": "skipped"}
    if not args.skip_resources:
        try:
            if args.metrics_settle_seconds:
                log(
                    f"waiting {args.metrics_settle_seconds:g}s for final Prometheus scrape"
                )
                time.sleep(args.metrics_settle_seconds)
            resources = benchmark_resources(args, load_start, load_end)
        except RuntimeError as error:
            resources = {"available": False, "reason": str(error)}

    report = {
        "environment": environment,
        "configuration": {
            "model": args.model,
            "ollama_url": args.ollama_url,
            "prometheus_url": args.prometheus_url,
            "context_length": args.context_length,
            "quality_runs": args.quality_runs,
            "warm_runs": args.warm_runs,
            "sustained_runs": args.sustained_runs,
            "output_token_limit": args.output_tokens,
            "concurrency": 0 if args.skip_concurrency else args.concurrency,
            "requests_per_client": args.requests_per_client,
            "temperature": 0,
            "seed": 42,
            "thinking": False,
        },
        "accuracy": accuracy,
        "cold_start": cold_start,
        "context_scaling": context,
        "warm_generation": warm,
        "sustained_load": sustained,
        "concurrency": concurrency,
        "deployment": deployment_state(args),
        "resources": resources,
        "limitations": [
            "Functional accuracy is a deterministic smoke suite, not a substitute for a standardized domain benchmark.",
            "Ollama token counts and internal durations are authoritative for this server but are not directly comparable across tokenizers.",
            "Prometheus resource values are sampled and estimated; brief peaks between scrapes can be missed.",
            "Results include other activity on the host during the measurement window.",
            "Concurrent latency includes server queueing; record OLLAMA_NUM_PARALLEL when comparing runs.",
        ],
    }
    report["benchmark_wall_seconds"] = time.perf_counter() - benchmark_started
    report["summary"] = make_summary(report)
    serialized = json.dumps(rounded(report), indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as output_file:
            output_file.write(serialized + "\n")
        log(f"wrote report to {args.output}")
    print(serialized)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, KeyboardInterrupt) as error:
        log(f"error: {error}")
        raise SystemExit(1) from error
