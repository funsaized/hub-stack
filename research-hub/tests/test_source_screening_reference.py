"""Guards the HUB-038 calibration: the reference set and the numbers it decided.

Fully offline and deterministic. These tests exist so the deployed
``PLAN_SOURCE_RELEVANCE`` cannot drift away from the evidence that chose it,
and so a change to the scoring function has to confront the measurement rather
than silently invalidate it.
"""

import pytest

from app.config import Config
from tests.benchmark_source_screening import (
    DECIDED_LABELS,
    load,
    sweep,
    variant_auc,
)

ROWS = [r for r in load() if r["label"] in DECIDED_LABELS]


def _config(**overrides):
    base = dict(
        redis_url="redis://localhost:6379/0", qdrant_url="http://q",
        ollama_url="http://o", llm_model="m", embedding_model="e",
        searxng_url="http://s", crawl4ai_url="http://c", crawl4ai_token="t",
        log_level="info", judge_api_key="placeholder",
    )
    base.update(overrides)
    return Config(**base)


def test_reference_set_is_intact():
    assert len(ROWS) == 455
    counts = {label: sum(1 for r in ROWS if r["label"] == label)
              for label in DECIDED_LABELS}
    assert counts == {"on_topic": 280, "marginal": 113, "off_topic": 62}
    assert len({r["topic"] for r in ROWS}) == 20


def test_every_row_carries_the_deployed_score():
    assert all(r.get("windowed_topic_score") is not None for r in ROWS)


def test_the_deployed_variant_separates_on_from_off_topic():
    score, n = variant_auc(ROWS, "windowed_topic_score")
    assert n == 342
    assert score == pytest.approx(0.875, abs=0.005)


def test_facet_anchoring_is_worse_and_stays_reverted():
    """The reverted variant, pinned so the regression cannot quietly return."""
    paired = [r for r in ROWS if r.get("windowed_facet_score") is not None]
    topic, _ = variant_auc(paired, "windowed_topic_score")
    facet, _ = variant_auc(paired, "windowed_facet_score")
    assert facet < topic - 0.08, (facet, topic)


def test_windowing_is_within_noise_of_the_opening_probe():
    """Recorded honestly: windowing is kept for robustness, not for accuracy."""
    paired = [r for r in ROWS if r.get("opening_score") is not None]
    opening, _ = variant_auc(paired, "opening_score")
    windowed, _ = variant_auc(paired, "windowed_topic_score")
    assert abs(windowed - opening) < 0.02


def test_the_deployed_threshold_is_the_one_the_sweep_justifies():
    deployed = _config().plan_source_relevance
    row = next(r for r in sweep(ROWS, "windowed_topic_score")
               if r["threshold"] == pytest.approx(deployed))
    # Tuned for recall: a lost source cannot be recovered downstream.
    assert row["on_topic_kept"] >= 0.98
    assert row["off_topic_dropped"] >= 0.30
    assert row["on_topic_lost"] <= 6


def test_no_adequately_sampled_topic_is_badly_served():
    by_topic = {}
    for row in ROWS:
        by_topic.setdefault(row["topic"], []).append(row)
    deployed = _config().plan_source_relevance
    for topic, group in by_topic.items():
        on = [r["windowed_topic_score"] for r in group
              if r["label"] == "on_topic"]
        if len(on) < 5:
            continue
        kept = sum(1 for x in on if x >= deployed) / len(on)
        assert kept >= 0.90, (topic, kept)
