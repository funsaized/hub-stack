#!/usr/bin/env python3
import json
import math
import time
import urllib.parse
import urllib.request


OLLAMA = "http://127.0.0.1:11434"
PROMETHEUS = "http://127.0.0.1:9090"
MODEL = "qwen3.5:9b"


def request_json(url, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.load(response)


def generate(prompt, num_predict):
    return request_json(
        f"{OLLAMA}/api/generate",
        {
            "model": MODEL,
            "prompt": prompt,
            "stream": False,
            "think": False,
            "keep_alive": "30m",
            "options": {
                "num_ctx": 8192,
                "num_predict": num_predict,
                "temperature": 0,
            },
        },
    )


def grade(value, thresholds, higher_is_better=True):
    grades = "ABCD"
    for index, threshold in enumerate(thresholds):
        if (value >= threshold) if higher_is_better else (value <= threshold):
            return grades[index]
    return "F"


def tokens_per_second(result):
    return result["eval_count"] / (result["eval_duration"] / 1_000_000_000)


def prometheus_peak(query, start, end):
    params = urllib.parse.urlencode(
        {"query": query, "start": start, "end": end, "step": 1}
    )
    result = request_json(f"{PROMETHEUS}/api/v1/query_range?{params}")
    values = [
        float(sample[1])
        for series in result["data"]["result"]
        for sample in series["values"]
        if math.isfinite(float(sample[1]))
    ]
    if not values:
        raise RuntimeError(f"Prometheus returned no samples for {query}")
    return max(values)


def main():
    checks = [
        ("739 multiplied by 864. Reply with digits only.", "638496"),
        (
            "A snail climbs 3 meters by day and slips 2 meters by night in a "
            "10-meter well. Reply only with the escape day number.",
            "8",
        ),
        (
            'Reply with exactly this JSON and nothing else: '
            '{"status":"ok","sum":42}',
            '{"status":"ok","sum":42}',
        ),
        (
            "All norps are flims. No flims are daxes. Can any norp be a dax? "
            "Reply only yes or no.",
            "no",
        ),
    ]

    request_json(
        f"{OLLAMA}/api/generate",
        {"model": MODEL, "prompt": "", "stream": False, "keep_alive": 0},
    )
    time.sleep(1)

    cold = generate(checks[0][0], 32)
    cold_load = cold["load_duration"] / 1_000_000_000

    quality_results = [(cold["response"].strip(), checks[0][1])]
    for prompt, expected in checks[1:]:
        result = generate(prompt, 64)
        quality_results.append((result["response"].strip(), expected))

    long_prompt = " ".join(
        f"item-{index}:local-inference-context" for index in range(2_500)
    )
    prefill = generate(f"{long_prompt}\nReply only OK.", 8)
    prefill_speed = prefill["prompt_eval_count"] / (
        prefill["prompt_eval_duration"] / 1_000_000_000
    )

    throughput_prompt = (
        "In about 180 words, explain why full GPU offload improves local LLM "
        "inference performance. Do not use a list."
    )
    warm_results = [generate(throughput_prompt, 256) for _ in range(3)]
    speeds = [tokens_per_second(result) for result in warm_results]
    average_speed = sum(speeds) / len(speeds)

    load_start = time.time()
    load_results = [generate(throughput_prompt, 256) for _ in range(16)]
    load_end = time.time()
    sustained_speed = sum(tokens_per_second(result) for result in load_results) / len(
        load_results
    )

    time.sleep(16)
    metrics_end = time.time()
    metrics = {
        "peak_gpu_utilization": prometheus_peak(
            "nvidia_smi_utilization_gpu_ratio", load_start, metrics_end
        ),
        "peak_vram_gib": prometheus_peak(
            "nvidia_smi_memory_used_bytes / 1024 / 1024 / 1024",
            load_start,
            metrics_end,
        ),
        "peak_gpu_temperature_c": prometheus_peak(
            "nvidia_smi_temperature_gpu", load_start, metrics_end
        ),
        "peak_gpu_power_w": prometheus_peak(
            "nvidia_smi_power_draw_watts", load_start, metrics_end
        ),
        "peak_host_cpu_busy": prometheus_peak(
            '1 - avg(rate(node_cpu_seconds_total{mode="idle"}[30s]))',
            load_start,
            metrics_end,
        ),
        "peak_host_memory_used": prometheus_peak(
            "1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)",
            load_start,
            metrics_end,
        ),
    }

    running = request_json(f"{OLLAMA}/api/ps")["models"][0]
    full_gpu = running["size_vram"] == running["size"]
    passed = sum(actual.lower() == expected.lower() for actual, expected in quality_results)

    results = {
        "model": MODEL,
        "quality": {
            "passed": f"{passed}/{len(checks)}",
            "grade": grade(passed / len(checks), [1.0, 0.75, 0.5, 0.25]),
            "answers": [
                {"actual": actual, "expected": expected}
                for actual, expected in quality_results
            ],
        },
        "cold_load": {
            "seconds": round(cold_load, 2),
            "grade": grade(cold_load, [10, 20, 40, 60], higher_is_better=False),
        },
        "warm_generation": {
            "tokens_per_second": round(average_speed, 1),
            "runs": [round(speed, 1) for speed in speeds],
            "grade": grade(average_speed, [100, 75, 50, 25]),
        },
        "prompt_ingestion": {
            "tokens": prefill["prompt_eval_count"],
            "tokens_per_second": round(prefill_speed, 1),
            "grade": grade(prefill_speed, [500, 350, 200, 100]),
        },
        "sustained_generation": {
            "tokens_per_second": round(sustained_speed, 1),
            "seconds": round(load_end - load_start, 1),
            "grade": grade(sustained_speed, [100, 75, 50, 25]),
        },
        "offload": {
            "full_gpu": full_gpu,
            "context": running["context_length"],
            "grade": "A" if full_gpu and running["context_length"] == 8192 else "F",
        },
        "resources": {
            "peak_gpu_utilization_percent": round(
                metrics["peak_gpu_utilization"] * 100, 1
            ),
            "peak_vram_gib": round(metrics["peak_vram_gib"], 2),
            "peak_gpu_temperature_c": round(metrics["peak_gpu_temperature_c"], 1),
            "peak_gpu_power_w": round(metrics["peak_gpu_power_w"], 1),
            "peak_host_cpu_busy_percent": round(metrics["peak_host_cpu_busy"] * 100, 1),
            "peak_host_memory_used_percent": round(
                metrics["peak_host_memory_used"] * 100, 1
            ),
            "thermal_grade": grade(
                metrics["peak_gpu_temperature_c"],
                [75, 82, 85, 90],
                higher_is_better=False,
            ),
        },
    }
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
